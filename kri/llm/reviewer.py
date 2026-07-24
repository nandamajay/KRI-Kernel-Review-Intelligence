"""Intelligent Review Engine — multi-agent orchestrator."""

from __future__ import annotations

import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Literal

from kri.common.models import Patch, PatchSeries, Severity
from kri.llm.agents import CodeQualityAgent, PatchSummarizerAgent, SubsystemExpertAgent
from kri.llm.client import LLMClient, LLMConfig, LLMOfflineError
from kri.llm.formatter import extract_hunk_context, format_lore_reply
from kri.llm.models import (
    AgentReviewOutput,
    InlineComment,
    IntelligentReport,
    PatchReview,
    PatchSummary,
)
from kri.llm.prompts import AGGREGATE_REVIEW_PROMPT, SYSTEM_KERNEL_REVIEWER, build_domain_context
from kri.llm.sanitize import strip_trailers
from kri.series import (
    SeriesReducer,
    SeriesReviewContext,
    SeriesReviewContextBuilder,
    format_series_context,
)

logger = logging.getLogger(__name__)


class IntelligentReviewEngine:
    """Orchestrates multiple LLM review agents to produce a comprehensive review."""

    def __init__(
        self,
        client: LLMClient | None = None,
        config: LLMConfig | None = None,
        dkp: Any | None = None,
        static_analysis: Any | None = None,
        series_awareness: bool = True,
        series_context_builder: SeriesReviewContextBuilder | None = None,
        series_reducer_mode: Literal["off", "shadow", "on"] = "off",
        series_reducer: SeriesReducer | None = None,
        series_r5_enabled: bool = True,
        series_r6_enabled: bool = True,
        series_r7_enabled: bool = True,
    ) -> None:
        self._client = client or LLMClient(config or LLMConfig())
        self._dkp = dkp
        self._static_analysis = static_analysis
        self._series_awareness = series_awareness
        self._series_context_builder = (
            series_context_builder or SeriesReviewContextBuilder()
        ) if series_awareness else None
        # WP-S1B: series reducer is *always* instantiated but is a no-op
        # in mode="off" (the default). Feature-flag geometry per readiness §6.1.
        self._series_reducer = series_reducer or SeriesReducer()
        self._series_reducer_mode: Literal["off", "shadow", "on"] = series_reducer_mode
        self._series_reducer_flags: dict[str, bool] = {
            "series_r5_enabled": series_r5_enabled,
            "series_r6_enabled": series_r6_enabled,
            "series_r7_enabled": series_r7_enabled,
        }
        self._domain_context = ""
        if dkp:
            rules = dkp.rules() if hasattr(dkp, "rules") else None
            patterns = dkp.patterns() if hasattr(dkp, "patterns") else None
            self._domain_context = build_domain_context(rules, patterns)

    def review(self, series: PatchSeries) -> IntelligentReport:
        """Run all agents on every patch in the series."""
        start = time.monotonic()

        series_ctx: SeriesReviewContext | None = None
        if self._series_context_builder is not None:
            series_ctx = self._series_context_builder.build(series)

        # Process patches concurrently (each patch spawns its own agent threads).
        with ThreadPoolExecutor(max_workers=min(len(series.patches), 4)) as pool:
            futures = [
                pool.submit(self._review_patch, patch, series, series_ctx)
                for patch in series.patches
            ]
            patch_reviews = [f.result() for f in futures]

        overall = self._generate_overall_assessment(patch_reviews)
        full_lore = "\n\n---\n\n".join(pr.lore_reply for pr in patch_reviews if pr.lore_reply)
        elapsed = time.monotonic() - start

        total_checkpatch = sum(
            len(pr.metadata.get("checkpatch_findings", []))
            for pr in patch_reviews
            if pr.metadata
        )

        metadata: dict[str, Any] = {
            "llm_model": self._client._cfg.model,
            "llm_stats": self._client.stats,
            "processing_time_seconds": round(elapsed, 1),
            "checkpatch_finding_count": total_checkpatch,
        }
        if series_ctx is not None and series_ctx.is_multi_patch():
            metadata["series_context"] = series_ctx.to_metadata()

        return IntelligentReport(
            series_id=series.series_id,
            series_title=series.title,
            patches=patch_reviews,
            overall_assessment=overall,
            lore_reply=full_lore,
            metadata=metadata,
        )

    def _review_patch(
        self,
        patch: Patch,
        series: PatchSeries,
        series_ctx: SeriesReviewContext | None = None,
    ) -> PatchReview:
        """Run all agents on a single patch, aggregate results."""
        summarizer = PatchSummarizerAgent(self._client)
        code_quality = CodeQualityAgent(self._client, self._domain_context)
        subsystem = SubsystemExpertAgent(self._client, self._domain_context)

        # Run checkpatch before agent threads so findings are available as prompt grounding.
        checkpatch_findings: list[dict] = []
        if self._static_analysis is not None:
            try:
                checkpatch_findings = self._static_analysis.run_checkpatch(patch)
            except Exception as e:
                logger.warning("checkpatch failed: %s", e)

        summary: PatchSummary | None = None
        agent_outputs: list[AgentReviewOutput] = []

        series_context_block = ""
        if series_ctx is not None:
            series_context_block = format_series_context(series_ctx, patch.patch_id)

        # Run agents in parallel using threads
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(summarizer.analyze, patch, series): "summarizer",
                pool.submit(
                    code_quality.review, patch, series, checkpatch_findings,
                    series_context_block,
                ): "code_quality",
                pool.submit(
                    subsystem.review, patch, series, checkpatch_findings,
                    series_context_block,
                ): "subsystem",
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
        # WP-S1B diagnostics: capture the pre-merge cross-agent overlap
        # BEFORE _merge_comments collapses (file, line, category)
        # duplicates. This is the only place the raw 3-agent output is
        # visible; once _merge_comments runs, overlap is invisible to
        # any downstream rule (R4 in particular) and the "did the
        # reducer have work?" question becomes unanswerable.
        agent_overlap = self._measure_agent_overlap(agent_outputs)
        all_comments = self._merge_comments(agent_outputs)

        # Back-fill hunk_context deterministically — do not rely on LLM to populate it.
        # The LLM inconsistently fills this field; extract it from the diff instead.
        diff_lines = patch.diff.split("\n")
        for comment in all_comments:
            if not comment.hunk_context:
                lines = extract_hunk_context(diff_lines, comment.file_path, comment.line_number)
                comment.hunk_context = "\n".join(lines)

        # WP-S1B Step B1: series reducer runs AFTER _merge_comments + hunk_context
        # back-fill, BEFORE PatchReview assembly (authoritative ordering per
        # readiness review §7.B1). mode="off" is a pure no-op — the reducer
        # returns its input unchanged and no evaluator runs, guaranteeing
        # byte-identity with the pre-B1 path.
        reducer_result = self._series_reducer.reduce(
            patch_id=patch.patch_id,
            comments=all_comments,
            series_ctx=series_ctx,
            mode=self._series_reducer_mode,
            flags=self._series_reducer_flags,
            diff=patch.diff or "",
        )
        all_comments = reducer_result.comments

        # Generate lore-style reply
        lore_reply = format_lore_reply(patch, summary, all_comments)

        real_findings = [f for f in checkpatch_findings if not f.get("degraded")]
        pr_metadata: dict[str, Any] = {}
        if real_findings:
            pr_metadata["checkpatch_findings"] = real_findings
        if series_ctx is not None and series_ctx.is_multi_patch():
            entry = series_ctx.patch_index.get(patch.patch_id)
            if entry is not None:
                pr_metadata["series_index"] = {
                    "index": entry.index,
                    "total": entry.total,
                }
        if reducer_result.actions:
            pr_metadata["series_reducer_actions"] = [
                a.to_metadata() for a in reducer_result.actions
            ]
        # WP-S1B diagnostics — always emitted when the reducer ran
        # (any non-``off`` mode). ``agent_overlap`` was captured above
        # from the raw agent outputs before _merge_comments collapsed
        # them; ``reducer_result.diagnostics`` was computed by the
        # reducer over the merged list. Together they answer "was
        # there any input for the rules?" and "did any rule's
        # precondition class appear in the input?" — the two questions
        # the 6-batch shadow run left unanswered.
        #
        # The gate is on mode only, NOT on series_ctx: a shadow run
        # over a single-patch series still produces (all-zero) counters
        # so scraper tooling never has to guess "did the reducer run at
        # all vs find nothing".
        if self._series_reducer_mode != "off":
            reducer_diag: dict[str, Any] = dict(reducer_result.diagnostics.to_metadata())
            reducer_diag.update(agent_overlap)
            pr_metadata["reducer_diagnostics"] = reducer_diag
        return PatchReview(
            patch_id=patch.patch_id,
            subject=patch.subject,
            summary=summary,
            inline_comments=all_comments,
            general_comments=self._collect_general(agent_outputs),
            lore_reply=lore_reply,
            metadata=pr_metadata,
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

    @staticmethod
    def _measure_agent_overlap(outputs: list[AgentReviewOutput]) -> dict[str, Any]:
        """Measure how much the parallel review-agent stream overlaps on
        ``(file, line // 10)``.

        Emitted before ``_merge_comments`` collapses (file, line,
        category) duplicates — this is the ONLY point in the pipeline
        where the raw per-agent geometry is visible. Once merged, we
        can no longer tell whether R4 has "nothing to bucket because
        agents diverged" vs "nothing to bucket because floor swallowed
        everything".

        Note: KRI's engine spawns three threads per patch, but only two
        (``code_quality`` and ``subsystem``) produce inline comments —
        ``summarizer`` produces a :class:`PatchSummary`, not a review
        output. The counters below therefore reflect *review-agent*
        overlap, not thread overlap.

        Returned counters:
          - ``per_agent_finding_counts``: comma-joined "N,M" of the
            raw agent output sizes (order = ``outputs`` order, which
            comes from ``futures.as_completed`` — non-deterministic).
          - ``total_line_buckets``: distinct (file, line // 10) buckets
            observed across all agents. Needed as a denominator by any
            downstream metric.
          - ``cross_agent_line_bucket_count``: buckets that received a
            finding from ≥ 2 distinct agents.
          - ``cross_agent_line_bucket_pct``: multi-agent-bucket-count
            as a percent of ``total_line_buckets`` (0..100 rounded).
            0 means every bucket saw at most one agent — R4 has no
            volume by construction.

        Confidence-cutoff mirrors ``_merge_comments`` (< 0.4 skipped)
        so counts reflect what would actually reach the reducer.
        """
        per_agent_counts: list[int] = []
        buckets: dict[tuple[str, int], set[int]] = {}
        for agent_idx, output in enumerate(outputs):
            per_agent_counts.append(len(output.inline_comments))
            for comment in output.inline_comments:
                if comment.confidence < 0.4:
                    continue
                key = (comment.file_path, comment.line_number // 10)
                buckets.setdefault(key, set()).add(agent_idx)

        total_buckets = len(buckets)
        multi_agent = sum(1 for agents in buckets.values() if len(agents) >= 2)
        pct = round((multi_agent / total_buckets) * 100.0, 1) if total_buckets else 0.0

        return {
            "per_agent_finding_counts": ",".join(str(n) for n in per_agent_counts),
            "total_line_buckets": total_buckets,
            "cross_agent_line_bucket_count": multi_agent,
            "cross_agent_line_bucket_pct": pct,
        }

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
            return strip_trailers(resp.content.strip())
        except Exception as e:
            logger.warning("Assessment generation failed: %s", e)
            n_blockers = sum(1 for pr in patch_reviews for c in pr.inline_comments if c.severity == "blocker")
            return f"Found {len(all_comments)} issues ({n_blockers} blockers) across {len(patch_reviews)} patches."
