"""Review agents — focused LLM calls for different review aspects."""

from __future__ import annotations

import json
import logging
from typing import Any

from kri.common.models import Patch, PatchSeries, Severity
from kri.llm.client import LLMClient, LLMOfflineError
from kri.llm.models import AgentReviewOutput, InlineComment, PatchSummary
from kri.llm.prompts import (
    REVIEW_CODE_QUALITY_PROMPT,
    REVIEW_SUBSYSTEM_PROMPT,
    SUMMARIZE_PATCH_PROMPT,
    SYSTEM_KERNEL_REVIEWER,
    annotate_diff_with_line_numbers,
    build_domain_context,
)

logger = logging.getLogger(__name__)

_SEVERITY_MAP = {"blocker": Severity.BLOCKER, "warning": Severity.WARNING, "info": Severity.INFO}


def _parse_severity(s: str) -> Severity:
    return _SEVERITY_MAP.get(s.lower(), Severity.INFO)


def _truncate_at_line_boundary(text: str, max_chars: int) -> str:
    """Truncate text at a newline boundary to avoid cutting mid-line."""
    if len(text) <= max_chars:
        return text
    cut = text.rfind("\n", 0, max_chars)
    if cut <= 0:
        cut = max_chars
    return text[:cut]


class PatchSummarizerAgent:
    """Explains what a patch does."""

    name = "summarizer"

    def __init__(self, client: LLMClient) -> None:
        self._client = client

    def analyze(self, patch: Patch, series: PatchSeries) -> PatchSummary | None:
        prompt = SUMMARIZE_PATCH_PROMPT.format(
            commit_message=patch.commit_message[:2000],
            files_changed="\n".join(patch.files_changed),
            cover_letter=(series.cover_letter or "")[:1500],
            diff=_truncate_at_line_boundary(patch.diff, 8000),
        )
        try:
            data = self._client.complete_json(
                [{"role": "user", "content": prompt}],
                system=SYSTEM_KERNEL_REVIEWER,
                max_tokens=1024,
            )
            return PatchSummary.model_validate(data)
        except (LLMOfflineError, json.JSONDecodeError, Exception) as e:
            logger.warning("Summarizer failed: %s", e)
            return None


class CodeQualityAgent:
    """Finds bugs, error handling issues, resource leaks."""

    name = "code_quality"

    def __init__(self, client: LLMClient, domain_context: str = "") -> None:
        self._client = client
        self._domain_context = domain_context

    def review(self, patch: Patch, series: PatchSeries) -> AgentReviewOutput:
        annotated = annotate_diff_with_line_numbers(patch.diff)
        prompt = REVIEW_CODE_QUALITY_PROMPT.format(
            domain_context=self._domain_context,
            commit_message=patch.commit_message[:2000],
            annotated_diff=_truncate_at_line_boundary(annotated, 12000),
        )
        comments: list[InlineComment] = []
        try:
            data = self._client.complete_json(
                [{"role": "user", "content": prompt}],
                system=SYSTEM_KERNEL_REVIEWER,
                max_tokens=4096,
            )
            if isinstance(data, list):
                for item in data:
                    comments.append(self._parse_comment(item))
        except (LLMOfflineError, json.JSONDecodeError, Exception) as e:
            logger.warning("CodeQuality agent failed: %s", e)

        return AgentReviewOutput(
            agent_name=self.name,
            patch_id=patch.patch_id,
            inline_comments=comments,
            confidence=_avg_confidence(comments),
        )

    @staticmethod
    def _parse_comment(item: dict[str, Any]) -> InlineComment:
        return InlineComment(
            file_path=item.get("file_path", ""),
            line_number=int(item.get("line_number", 0)),
            category=item.get("category", "bug"),
            severity=_parse_severity(item.get("severity", "warning")),
            message=item.get("message", ""),
            suggestion=item.get("suggestion"),
            confidence=float(item.get("confidence", 0.5)),
            reasoning=item.get("reasoning", ""),
        )


class SubsystemExpertAgent:
    """Checks subsystem conventions, API usage, design patterns."""

    name = "subsystem_expert"

    def __init__(self, client: LLMClient, domain_context: str = "") -> None:
        self._client = client
        self._domain_context = domain_context

    def review(self, patch: Patch, series: PatchSeries) -> AgentReviewOutput:
        annotated = annotate_diff_with_line_numbers(patch.diff)
        prompt = REVIEW_SUBSYSTEM_PROMPT.format(
            domain_context=self._domain_context,
            commit_message=patch.commit_message[:2000],
            files_changed="\n".join(patch.files_changed),
            annotated_diff=_truncate_at_line_boundary(annotated, 12000),
        )
        comments: list[InlineComment] = []
        try:
            data = self._client.complete_json(
                [{"role": "user", "content": prompt}],
                system=SYSTEM_KERNEL_REVIEWER,
                max_tokens=4096,
            )
            if isinstance(data, list):
                for item in data:
                    comments.append(InlineComment(
                        file_path=item.get("file_path", ""),
                        line_number=int(item.get("line_number", 0)),
                        category=item.get("category", "convention"),
                        severity=_parse_severity(item.get("severity", "warning")),
                        message=item.get("message", ""),
                        suggestion=item.get("suggestion"),
                        confidence=float(item.get("confidence", 0.5)),
                        reasoning=item.get("reasoning", ""),
                    ))
        except (LLMOfflineError, json.JSONDecodeError, Exception) as e:
            logger.warning("SubsystemExpert agent failed: %s", e)

        return AgentReviewOutput(
            agent_name=self.name,
            patch_id=patch.patch_id,
            inline_comments=comments,
            confidence=_avg_confidence(comments),
        )


def _avg_confidence(comments: list[InlineComment]) -> float:
    if not comments:
        return 0.0
    return sum(c.confidence for c in comments) / len(comments)
