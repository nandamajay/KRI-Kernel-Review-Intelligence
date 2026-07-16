"""Claude API client for KRI intelligent review.

Thin wrapper around the Anthropic Messages API exposed via Qualcomm's
QGenie gateway. Handles auth, retries, timeouts, and offline mode.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from typing import Any

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)


class LLMConfig:
    """Configuration for the LLM client."""

    def __init__(
        self,
        api_url: str | None = None,
        api_key: str | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.2,
        timeout: int = 120,
        max_retries: int = 3,
        offline: bool = False,
    ) -> None:
        self.api_url = api_url or os.environ.get(
            "KRI_LLM_API_URL",
            "https://qgenie-api.qualcomm.com/v1/messages",
        )
        self.api_key = api_key or os.environ.get("ANTHROPIC_AUTH_TOKEN", "")
        self.model = model or os.environ.get(
            "KRI_LLM_MODEL", "anthropic::claude-4-6-sonnet"
        )
        self.max_tokens = max_tokens
        self.temperature = temperature
        self.timeout = timeout
        self.max_retries = max_retries
        self.offline = offline or os.environ.get("KRI_LLM_OFFLINE", "") == "1"


class LLMResponse:
    """Structured response from the LLM API."""

    def __init__(self, content: str, model: str, usage: dict[str, int] | None = None):
        self.content = content
        self.model = model
        self.usage = usage or {}

    @property
    def input_tokens(self) -> int:
        return self.usage.get("input_tokens", 0)

    @property
    def output_tokens(self) -> int:
        return self.usage.get("output_tokens", 0)


class LLMOfflineError(RuntimeError):
    """Raised when LLM is called in offline mode."""


class LLMClient:
    """Synchronous Claude API client with retries and structured output."""

    def __init__(self, config: LLMConfig | None = None) -> None:
        self._cfg = config or LLMConfig()
        self._total_input_tokens = 0
        self._total_output_tokens = 0
        self._call_count = 0
        self._lock = threading.Lock()

    @property
    def stats(self) -> dict[str, int]:
        with self._lock:
            return {
                "calls": self._call_count,
                "input_tokens": self._total_input_tokens,
                "output_tokens": self._total_output_tokens,
            }

    def complete(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> LLMResponse:
        """Send a completion request to the Claude API.

        Returns the text content of the first response block.
        Retries on transient errors (429, 500, 502, 503).
        """
        if self._cfg.offline:
            raise LLMOfflineError("LLM client is in offline mode")

        if not self._cfg.api_key:
            raise LLMOfflineError("No API key set (ANTHROPIC_AUTH_TOKEN)")

        payload: dict[str, Any] = {
            "model": self._cfg.model,
            "max_tokens": max_tokens if max_tokens is not None else self._cfg.max_tokens,
            "messages": messages,
        }
        if system:
            payload["system"] = system
        temp = temperature if temperature is not None else self._cfg.temperature
        if temp is not None:
            payload["temperature"] = temp

        headers = {
            "x-api-key": self._cfg.api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        }

        last_error: Exception | None = None
        for attempt in range(self._cfg.max_retries):
            try:
                resp = requests.post(
                    self._cfg.api_url,
                    headers=headers,
                    json=payload,
                    timeout=self._cfg.timeout,
                    verify=False,
                )
                if resp.status_code == 200:
                    data = resp.json()
                    content = ""
                    for block in data.get("content", []):
                        if block.get("type") == "text":
                            content += block.get("text", "")
                    usage = data.get("usage", {})
                    with self._lock:
                        self._call_count += 1
                        self._total_input_tokens += usage.get("input_tokens", 0)
                        self._total_output_tokens += usage.get("output_tokens", 0)
                    return LLMResponse(
                        content=content,
                        model=data.get("model", self._cfg.model),
                        usage=usage,
                    )
                if resp.status_code in (429, 500, 502, 503):
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "LLM API %d on attempt %d, retrying in %ds",
                        resp.status_code, attempt + 1, wait,
                    )
                    time.sleep(wait)
                    last_error = RuntimeError(
                        f"HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                    continue
                resp.raise_for_status()
            except requests.Timeout as e:
                last_error = e
                if attempt < self._cfg.max_retries - 1:
                    time.sleep(2 ** (attempt + 1))
                    continue
                break
            except requests.ConnectionError as e:
                last_error = e
                break

        raise RuntimeError(f"LLM API failed after {self._cfg.max_retries} attempts: {last_error}")

    def complete_json(
        self,
        messages: list[dict[str, str]],
        system: str | None = None,
        max_tokens: int | None = None,
    ) -> Any:
        """Call complete() and parse the response as JSON.

        Strips markdown code fences if present. Finds JSON in surrounding
        prose. Returns parsed JSON object.
        """
        resp = self.complete(messages, system=system, max_tokens=max_tokens)
        text = resp.content.strip()
        return self._extract_json(text)

    @staticmethod
    def _extract_json(text: str) -> Any:
        """Extract and parse JSON from LLM response text."""
        # Try direct parse first.
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Strip markdown code fences.
        if "```" in text:
            m = re.search(r"```(?:json)?\s*\n(.*?)```", text, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1).strip())
                except json.JSONDecodeError:
                    pass

        # Find first [ or { and try to parse from there.
        # Use progressively shorter substrings from the end to handle
        # trailing prose after the JSON.
        for start_char, end_char in [("[", "]"), ("{", "}")]:
            start = text.find(start_char)
            if start == -1:
                continue
            # Try from each matching end char, last occurrence first.
            candidate = text[start:]
            end = len(candidate)
            while end > 0:
                end = candidate.rfind(end_char, 0, end)
                if end <= 0:
                    break
                try:
                    return json.loads(candidate[:end + 1])
                except json.JSONDecodeError:
                    continue

        raise json.JSONDecodeError("No valid JSON found in response", text, 0)
