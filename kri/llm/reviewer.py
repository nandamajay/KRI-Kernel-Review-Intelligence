"""Intelligent Review Engine — multi-agent orchestrator."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from kri.common.models import Patch, PatchSeries, Severity
from kri.llm.agents import CodeQualityAgent, PatchSummarizerAgent, SubsystemExpertAgent
from kri.llm.client import LLMClient, LLMConfig, LLMOfflineError
from kri.llm.formatter import format_lore_reply
from kri.llm.models import (
    AgentReviewOutput,
    InlineComment,
    IntelligentReport,
    PatchReview,
    PatchSummary,
)
from kri.llm.prompts import AGGREGATE_REVIEW_PROMPT, SYSTEM_KERNEL_REVIEWER, build_domain_context

logger = logging.getLogger(__name__)


class IntelligentReviewEngine:
    """Orchestrates multiple LLM review agents to produce a comprehensive review."""

    def __init__(
        self,
        client: LLMClient | None = None,
        config: LLMConfig | None = None,
        dkp: Any | None = None,
    ) -> None:
        self._client = client or LLMClient(config or LLMConfig())
        self._dkp = dkp
        self._domain_context = ""
        if dkp:
            rules = dkp.rules() if hasattr(dkp, "rules") else None
            patterns = dkp.patterns() if hasattr(dkp, "patterns") else None
            self._domain_context = build_domain_context(rules, patterns)

    def review(self, series: PatchSeries) -> IntelligentReport:
        """Run all agents on every patch in the series."""
        start = time.monotonic()

        # Process patches concurrently (each patch spawns its own agent threads).
        with ThreadPoolExecutor(max_workers=min(len(series.patches), 4)) as pool:
            futures = [pool.submit(self._review_patch, patch, series) for patch in series.patches]
            patch_reviews = [f.result() for f in futures]

        overall = self._generate_overall_assessment(patch_reviews)
        full_lore = "\n\n---\n\n".join(pr.lore_reply for pr in patch_reviews if pr.lore_reply)
        elapsed = time.monotonic() - start

        return IntelligentReport(
            series_id=series.series_id,
            series_title=series.title,
            patches=patch_reviews,
            overall_assessment=overall,
            lore_reply=full_lore,
            metadata={
                "llm_model": self._client._cfg.model,
                "llm_stats": self._client.stats,
                "processing_time_seconds": round(elapsed, 1),
            },
        )

    def _review_patch(self, patch: Patch, series: PatchSeries) -> PatchReview:
        """Run all agents on a single patch, aggregate results."""
        summarizer = PatchSummarizerAgent(self._client)
        code_quality = CodeQualityAgent(self._client, self._domain_context)
        subsystem = SubsystemExpertAgent(self._client, self._domain_context)

        summary: PatchSummary | None = None
        agent_outputs: list[AgentReviewOutput] = []

        # Run agents in parallel using threads
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(summarizer.analyze, patch, series): "summarizer",
                pool.submit(code_quality.review, patch, series): "code_quality",
                pool.submit(subsystem.review, patch, series): "subsystem",
            }
            for future in as_completed(futures):
                agent_name = futures[future]
                try:
                    result = future.result()
                    if agent_name == "summarizer":
                        summary = result
                    else:
                        if result is not None:
                            agent_outputs.append(result)
                except Exception as e:
                    logger.warning("Agent %s failed: %s", agent_name, e)

        # Merge and deduplicate comments
        all_comments = self._merge_comments(agent_outputs)

        # Generate lore-style reply
        lore_reply = format_lore_reply(patch, summary, all_comments)

        return PatchReview(
            patch_id=patch.patch_id,
            subject=patch.subject,
            summary=summary,
            inline_comments=all_comments,
            general_comments=self._collect_general(agent_outputs),
            lore_reply=lore_reply,
        )

    def _merge_comments(self, outputs: list[AgentReviewOutput]) -> list[InlineComment]:
        """Merge comments from all agents, deduplicate by location+category."""
        seen: dict[str, InlineComment] = {}
        for output in outputs:
            for comment in output.inline_comments:
                if comment.confidence < 0.4:
                    continue
                key = f"{comment.file_path}:{comment.line_number}:{comment.category}"
                existing = seen.get(key)
                if existing is None or comment.confidence > existing.confidence:
                    seen[key] = comment
        # Sort: blockers first, then by file and line
        result = sorted(
            seen.values(),
            key=lambda c: (
                0 if c.severity == Severity.BLOCKER else 1 if c.severity == Severity.WARNING else 2,
                c.file_path,
                c.line_number,
            ),
        )
        return result

    @staticmethod
    def _collect_general(outputs: list[AgentReviewOutput]) -> list[str]:
        comments: list[str] = []
        for o in outputs:
            comments.extend(o.general_comments)
        return comments

    def _generate_overall_assessment(self, patch_reviews: list[PatchReview]) -> str:
        """Use LLM to synthesize a brief overall assessment."""
        all_comments = []
        for pr in patch_reviews:
            for c in pr.inline_comments:
                all_comments.append(f"[{c.severity.value}] {c.file_path}:{c.line_number} - {c.message}")

        if not all_comments:
            return "No significant issues found. The patch series looks reasonable."

        summaries = []
        for pr in patch_reviews:
            if pr.summary:
                summaries.append(pr.summary.what_it_does)

        prompt = AGGREGATE_REVIEW_PROMPT.format(
            summary="\n".join(summaries) or "No summary available",
            issues_text="\n".join(all_comments[:20]),
            rule_findings="(none)",
        )
        try:
            resp = self._client.complete(
                [{"role": "user", "content": prompt}],
                system=SYSTEM_KERNEL_REVIEWER,
                max_tokens=512,
            )
            return resp.content.strip()
        except Exception as e:
            logger.warning("Assessment generation failed: %s", e)
            n_blockers = sum(1 for pr in patch_reviews for c in pr.inline_comments if c.severity == "blocker")
            return f"Found {len(all_comments)} issues ({n_blockers} blockers) across {len(patch_reviews)} patches."
