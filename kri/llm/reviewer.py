"""Intelligent Review Engine — multi-agent orchestrator."""

from __future__ import annotations

import hashlib
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Literal

from kri.common.models import (
    Decision,
    Evidence,
    EvidenceGraph,
    EvidenceSourceType,
    Patch,
    PatchSeries,
    Provenance,
    ReasoningLayer,
    SeriesContext,
    Severity,
)
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
from kri.governance import ConstitutionalRules, check_evidence_status, load_rules, log_governance_warnings
from kri.series import (
    SeriesReducer,
    SeriesReviewContext,
    SeriesReviewContextBuilder,
    format_series_context,
)
from kri.lore_manager.version_discovery import format_prior_version_context
from kri.review_engine.series_context import build_series_context

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WP4-A helpers: InlineComment -> Decision converter
# ---------------------------------------------------------------------------

# Mapping from InlineComment.category strings to ReasoningLayer enum values.
# This is the best-effort heuristic — all categories not explicitly listed
# fall back to SEMANTIC.
_CATEGORY_TO_LAYER: dict[str, ReasoningLayer] = {
    "api_usage": ReasoningLayer.SEMANTIC,
    "api": ReasoningLayer.SEMANTIC,
    "memory": ReasoningLayer.SEMANTIC,
    "locking": ReasoningLayer.SEMANTIC,
    "error_handling": ReasoningLayer.SEMANTIC,
    "null_check": ReasoningLayer.SEMANTIC,
    "style": ReasoningLayer.STRUCTURAL,
    "formatting": ReasoningLayer.STRUCTURAL,
    "documentation": ReasoningLayer.STRUCTURAL,
    "naming": ReasoningLayer.STRUCTURAL,
    "design": ReasoningLayer.DESIGN,
    "architecture": ReasoningLayer.DESIGN,
    "integration": ReasoningLayer.INTEGRATION,
    "kconfig": ReasoningLayer.INTEGRATION,
    "build": ReasoningLayer.INTEGRATION,
    "maintainability": ReasoningLayer.MAINTAINABILITY,
    "abi": ReasoningLayer.ECOSYSTEM,
    "ecosystem": ReasoningLayer.ECOSYSTEM,
}


def _comment_layer(category: str) -> ReasoningLayer:
    """Deterministic category -> ReasoningLayer mapping."""
    return _CATEGORY_TO_LAYER.get(category.lower().strip(), ReasoningLayer.SEMANTIC)


def _match_dkp_rule(comment: InlineComment, dkp: Any | None) -> str | None:
    """Best-effort: find a DKP rule_id whose category matches the comment.

    Matches on comment.category against rule.category (case-insensitive prefix).
    Returns the first matching rule_id, or None if no DKP or no match.
    Does NOT write to the EKG — read-only query.
    """
    if dkp is None:
        return None
    try:
        rules = dkp.rules() if hasattr(dkp, "rules") else None
        if not rules:
            return None
        cat_lower = comment.category.lower().strip()
        for rule in rules:
            rule_cat = getattr(rule, "category", "").lower().strip()
            if rule_cat and (cat_lower == rule_cat or cat_lower.startswith(rule_cat)):
                return rule.rule_id
    except Exception:  # noqa: BLE001 - DKP query failures degrade gracefully
        pass
    return None


def _llm_comment_to_decision(
    comment: InlineComment,
    patch: Patch,
    series: PatchSeries,
    dkp: Any | None = None,
) -> Decision:
    """Convert an LLM-generated InlineComment to a Decision for evidence gating.

    decision_id uses blake2b/8 of stable comment fields (§40 compliant — no
    uuid/random/time). evidence_graph is initialized as an empty-but-valid
    EvidenceGraph so downstream callers never receive None.

    This function is pure: same inputs -> same Decision. No EKG writes.
    """
    raw = f"{comment.file_path}:{comment.line_number}:{comment.category}:{comment.message}"
    decision_id = hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()

    rule_id = _match_dkp_rule(comment, dkp)
    layer = _comment_layer(comment.category)

    return Decision(
        decision_id=decision_id,
        series_id=series.series_id,
        patch_id=patch.patch_id,
        layer=layer,
        category=comment.category,
        severity=comment.severity,
        location=f"{comment.file_path}:{comment.line_number}",
        statement=comment.message,
        rule_id=rule_id,
        evidence_graph=EvidenceGraph(comment_id=decision_id),
    )


def _enrich_with_blame(
    evidence_graph: EvidenceGraph,
    file_path: str,
    line_number: int,
    repo_manager: Any,
) -> None:
    """Enrich an EvidenceGraph with BLAME_HISTORY evidence from git blame.

    WP4-E: Adds one Evidence node per commit found by blame. Each node's
    evidence_id is blake2b/8 of "{file}:{line}:{commit_hash}" — deterministic,
    §40 safe. Provenance.commit_hash is set so EvidenceEngineImpl.verify() will
    mark it verified.

    Mutates evidence_graph.evidence in place. Degrades gracefully on any error.
    Does NOT write to the EKG — blame evidence is ephemeral per-review context.
    """
    try:
        blame_entries = repo_manager.blame(file_path, line_number)
    except Exception:
        return
    for entry in blame_entries:
        commit_hash = entry.get("commit", "")
        if not commit_hash:
            continue
        raw = f"{file_path}:{line_number}:{commit_hash}"
        ev_id = hashlib.blake2b(raw.encode(), digest_size=8).hexdigest()
        summary = entry.get("summary", "")[:120]
        ev = Evidence(
            evidence_id=ev_id,
            source_type=EvidenceSourceType.BLAME_HISTORY,
            summary=summary or f"git blame: {file_path}:{line_number}",
            provenance=Provenance(commit_hash=commit_hash),
            verified=False,
            strength=0.0,
        )
        evidence_graph.evidence.append(ev)


def _enrich_with_lore_history(
    evidence_graph: EvidenceGraph,
    comment_category: str,
    review_history_store: Any,
) -> set[str]:
    """Enrich an EvidenceGraph with REVIEW_DISCUSSION evidence from the lore store.

    Track-B.5: Activates the REVIEW_HISTORY confidence factor by adding verified=True
    Evidence nodes from lore review history entries that match the comment's claim category.

    Matching strategy: normalize comment_category via _classify_claim() (same 19-pattern
    lexical matcher used at ingestion time) to get a canonical claim string, then call
    store.by_claim().  'review_discussion' fallback returns [] (BLOCK-4).

    Returns: set of series_ids of entries that contributed nodes (for post-hoc summary).
    Degrades gracefully on any error — never raises.
    """
    from kri.learning.ingestion import _classify_claim, lore_evidence_for_claim

    matched_series: set[str] = set()
    try:
        # Normalize to canonical claim vocabulary (BLOCK-1 resolution)
        normalized_claim, _ = _classify_claim(comment_category)
        evidence_nodes = lore_evidence_for_claim(normalized_claim, review_history_store)

        # Dedup against existing evidence_ids already in the graph
        existing_ids = {ev.evidence_id for ev in evidence_graph.evidence}
        for ev in evidence_nodes:
            if ev.evidence_id not in existing_ids:
                evidence_graph.evidence.append(ev)
                existing_ids.add(ev.evidence_id)

        # Collect matched series_ids directly from the matching entries (O(n), not O(n²))
        if evidence_nodes:
            for entry in review_history_store.by_claim(normalized_claim):
                if entry.source_url and entry.message_id:
                    matched_series.add(entry.series_id)

    except Exception as _exc:
        logger.debug("B5: _enrich_with_lore_history failed (non-fatal): %s", _exc)
    return matched_series



def _apply_status_to_dict(r: Any) -> dict:
    """Serialise an ``ApplicabilityResult`` to a plain dict for metadata storage."""
    return {
        "ok": r.ok,
        "degraded": r.degraded,
        "degraded_reason": r.degraded_reason,
        "baseline_ref": r.baseline_ref,
        "baseline_commit": r.baseline_commit,
        "failed": list(r.failed),
        "conflicts": list(r.conflicts),
        "duration_seconds": r.duration_seconds,
    }


def _summarize_apply_status(patch_reviews: list) -> dict:
    """Aggregate per-patch apply_status into a report-level summary."""
    total = 0
    clean = 0
    conflict = 0
    degraded = 0
    for pr in patch_reviews:
        if pr.metadata and "apply_status" in pr.metadata:
            total += 1
            s = pr.metadata["apply_status"]
            if s.get("degraded"):
                degraded += 1
            elif s.get("ok"):
                clean += 1
            else:
                conflict += 1
    return {"total": total, "clean": clean, "conflict": conflict, "degraded": degraded}


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
        gate: Any | None = None,
        baseline_ref: str = "HEAD",
        prior_version_fetcher: Any | None = None,
        evidence_engine: Any | None = None,
        knowledge_manager: Any | None = None,
        confidence_engine: Any | None = None,
        repo_manager: Any | None = None,
        review_history_store: Any | None = None,
        cfm_calibrator: Any | None = None,
    ) -> None:
        self._client = client or LLMClient(config or LLMConfig())
        self._dkp = dkp
        self._static_analysis = static_analysis
        self._gate = gate
        self._baseline_ref = baseline_ref
        self._prior_version_fetcher = prior_version_fetcher
        # WP4-B: evidence engine wiring (None = evidence gate disabled, mode-off safe)
        self._evidence_engine = evidence_engine
        self._knowledge_manager = knowledge_manager
        # WP4-C: CFM shadow mode (None = CFM scoring disabled, no gate effect)
        self._confidence_engine = confidence_engine
        # WP4-E: repo_manager for blame-backed evidence (None = blame disabled)
        self._repo_manager = repo_manager
        # WP4-J: Track-B lore review history store (None = no history, mode-off safe)
        self._review_history_store = review_history_store
        # WP4-K: Track-B CFM calibrator (None = no calibration)
        self._cfm_calibrator = cfm_calibrator
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
        self._governance_rules: ConstitutionalRules = ConstitutionalRules([])
        try:
            self._governance_rules = load_rules()
        except Exception as _gov_exc:
            logger.warning("Governance rules could not be loaded: %s", _gov_exc)
        self._domain_context = ""
        if dkp:
            rules = dkp.rules() if hasattr(dkp, "rules") else None
            patterns = dkp.patterns() if hasattr(dkp, "patterns") else None
            self._domain_context = build_domain_context(rules, patterns)

    def review(self, series: PatchSeries) -> IntelligentReport:
        """Run all agents on every patch in the series."""
        start = time.monotonic()

        # WP4-D: capture knowledge state BEFORE fan-out so all per-patch evidence
        # queries are stamped against the same immutable snapshot.
        knowledge_state_id: str | None = None
        if self._knowledge_manager is not None:
            try:
                ks = self._knowledge_manager.snapshot()
                knowledge_state_id = ks.state_id
            except Exception as _ks_exc:
                logger.warning("knowledge_manager.snapshot() failed: %s", _ks_exc)

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
        if self._gate is not None:
            metadata["apply_status_summary"] = _summarize_apply_status(patch_reviews)
        if knowledge_state_id is not None:
            metadata["knowledge_state_id"] = knowledge_state_id

        # WP4-J: Track-B review history summary (shadow mode, no gate effect)
        # Track-B.5: Collect matched lore series_ids from all patch reviews post-hoc.
        # This is thread-safe: all futures have joined before this line.
        # Only series that actually contributed Evidence nodes appear in the summary.
        all_lore_matched: set[str] = set()
        for pr in patch_reviews:
            if pr.metadata:
                for sid in pr.metadata.get("lore_matched_series", []):
                    all_lore_matched.add(sid)

        # WP4-J / Track-B.5: review_history_summary — filtered to matched series only.
        # Previously: global summarise() → all 129 series regardless of review content.
        # Now: only series that matched a comment's claim category during evidence enrichment.
        review_history_summary: list[dict] = []
        if self._review_history_store is not None:
            try:
                if all_lore_matched:
                    review_history_summary = [
                        s.model_dump()
                        for s in self._review_history_store.summarise_by_series_ids(all_lore_matched)
                    ]
                # else: no lore matches this review → empty summary (correct; no global leakage)
            except Exception as _rhs_exc:
                logger.warning("B5: review_history_store.summarise_by_series_ids() failed: %s", _rhs_exc)

        # WP4-K / Track-B.6: CFM shadow calibration (shadow mode, no gate effect).
        # Pass (comment_id, llm_confidence, claim_category) triples so the
        # calibrator can use claim-based evidence selection, matching the live path.
        cfm_calibration: dict | None = None
        if self._cfm_calibrator is not None:
            try:
                import hashlib as _hl
                llm_comments = [
                    (
                        _hl.sha256(
                            f"{c.file_path}:{c.line_number}:{c.message[:80]}".encode()
                        ).hexdigest()[:16],
                        c.confidence,
                        c.category,  # Track-B.6: claim category for per-claim evidence selection
                    )
                    for pr in patch_reviews
                    for c in pr.inline_comments
                ]
                calib_report = self._cfm_calibrator.calibrate(llm_comments)
                cfm_calibration = calib_report.model_dump()
            except Exception as _cfm_cal_exc:
                logger.warning("WP4-K: cfm_calibrator.calibrate() failed: %s", _cfm_cal_exc)

        return IntelligentReport(
            series_id=series.series_id,
            series_title=series.title,
            patches=patch_reviews,
            overall_assessment=overall,
            lore_reply=full_lore,
            metadata=metadata,
            review_history_summary=review_history_summary,
            cfm_calibration=cfm_calibration,
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

        # Strategy C: gate result is stored as metadata only, never injected into prompts.
        apply_status: dict | None = None
        if self._gate is not None:
            try:
                single = PatchSeries(
                    series_id=f"{series.series_id}:{patch.patch_id}",
                    patches=[patch],
                )
                gate_result = self._gate.check(single, baseline_ref=self._baseline_ref)
                apply_status = _apply_status_to_dict(gate_result)
            except Exception as _gate_exc:
                logger.warning("applicability gate failed: %s", _gate_exc)

        summary: PatchSummary | None = None
        agent_outputs: list[AgentReviewOutput] = []

        series_context_block = ""
        if series_ctx is not None:
            series_context_block = format_series_context(series_ctx, patch.patch_id)

        # WP-S2A: prior-version maintainer feedback injection.
        # Strategy C extension: apply_status is still NEVER injected (see below).
        prior_version_block = ""
        if self._prior_version_fetcher is not None:
            try:
                pairs = self._prior_version_fetcher.fetch(series)
                prior_version_block = format_prior_version_context(pairs, patch.patch_id)
            except Exception as _pv_exc:
                logger.warning("prior_version_fetcher failed: %s", _pv_exc)

        # Run agents in parallel using threads
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {
                pool.submit(summarizer.analyze, patch, series): "summarizer",
                pool.submit(
                    code_quality.review, patch, series, checkpatch_findings,
                    series_context_block, prior_version_block,
                ): "code_quality",
                pool.submit(
                    subsystem.review, patch, series, checkpatch_findings,
                    series_context_block, prior_version_block,
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

        # WP4-B: evidence gate — convert comments to Decisions, gather evidence,
        # apply evidence_status, suppress evidence_missing (non-safety-floor) comments.
        # Guard: only active when evidence_engine is wired in; None = mode-off safe.
        # WP4-V: pre-initialize so PatchReview() can always receive it, even when
        # evidence_engine is None (avoids NameError on the mode-off path).
        gov_violations: list[str] = []
        lore_matched_series: set[str] = set()
        if self._evidence_engine is not None:
            series_ctx_common: SeriesContext | None = None
            try:
                series_ctx_common = build_series_context(series)
            except Exception as _sc_exc:
                logger.warning("build_series_context failed: %s", _sc_exc)
            all_comments, lore_matched_series = self._apply_evidence_gate(
                all_comments, patch, series, series_ctx_common
            )
            # WP4-G: constitutional invariant check — evidence_missing BLOCKER/WARNING
            # must never appear in published output (§28/§35 safety floor guarantee).
            gov_violations = check_evidence_status(all_comments)
            for violation in gov_violations:
                logger.error("GOVERNANCE VIOLATION: %s", violation)

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
        if apply_status is not None:
            pr_metadata["apply_status"] = apply_status
        # Track-B.5: store matched lore series_ids in metadata for post-hoc summary assembly.
        # reviewer.review() collects these after ThreadPoolExecutor joins (thread-safe).
        if lore_matched_series:
            pr_metadata["lore_matched_series"] = sorted(lore_matched_series)
        return PatchReview(
            patch_id=patch.patch_id,
            subject=patch.subject,
            summary=summary,
            inline_comments=all_comments,
            general_comments=self._collect_general(agent_outputs),
            lore_reply=lore_reply,
            metadata=pr_metadata,
            governance_warnings=gov_violations,
        )

    def _apply_evidence_gate(
        self,
        comments: list[InlineComment],
        patch: Patch,
        series: PatchSeries,
        series_ctx_common: SeriesContext | None,
    ) -> tuple[list[InlineComment], set[str]]:
        """Apply the evidence gate to a list of merged comments.

        For each comment, converts it to a Decision, calls evidence_engine.gather(),
        and sets comment.evidence_status accordingly.

        Safety floor (§29 / §35): comments with severity in (BLOCKER, WARNING) AND
        confidence >= 0.70 are NEVER suppressed, regardless of evidence status.
        They receive evidence_status="safety_floored" when evidence is missing.

        Returns: (filtered_comments, matched_lore_series_ids).
        matched_lore_series_ids is the set of lore series_ids that contributed Evidence nodes
        during this gate pass; used by the caller to build a filtered review_history_summary.
        Thread-safe: caller assembles summary post-hoc after ThreadPoolExecutor joins.
        """
        result: list[InlineComment] = []
        all_matched_series: set[str] = set()
        for comment in comments:
            decision = _llm_comment_to_decision(comment, patch, series, self._dkp)
            try:
                evidence_graph = self._evidence_engine.gather(
                    decision, series_context=series_ctx_common
                )
            except Exception as _ev_exc:
                logger.warning("evidence gather failed for %s: %s", decision.decision_id, _ev_exc)
                evidence_graph = EvidenceGraph(comment_id=decision.decision_id)

            # WP4-E: enrich with BLAME_HISTORY evidence when repo_manager is wired.
            # Blame nodes get verified=True via commit_hash provenance (same logic as
            # EvidenceEngineImpl.verify()). This may promote evidence_missing → blame_backed.
            if self._repo_manager is not None:
                _enrich_with_blame(
                    evidence_graph, comment.file_path, comment.line_number, self._repo_manager
                )
                # Verify freshly-added blame evidence (commit_hash present → verified).
                for ev in evidence_graph.evidence:
                    if ev.source_type == EvidenceSourceType.BLAME_HISTORY and not ev.verified:
                        if ev.provenance.commit_hash and ev.provenance.commit_hash.strip():
                            ev.verified = True
                            # BLAME_HISTORY priority=9 → strength = max(0, 1-(9-1)*0.08) = 0.36
                            ev.strength = max(0.0, 1.0 - (9 - 1) * 0.08)

            # Track-B.5: enrich with REVIEW_DISCUSSION evidence from lore history store.
            # Sets verified=True (ephemeral enrichment — lore URLs are verifiable public records).
            # Activates REVIEW_HISTORY confidence factor (previously always 0.0).
            if self._review_history_store is not None:
                sids = _enrich_with_lore_history(
                    evidence_graph, comment.category, self._review_history_store
                )
                all_matched_series.update(sids)

            # WP4-C: CFM shadow mode — score is computed but never used as gate.
            # After B.5 enrichment, cfm_confidence now reflects per-comment lore evidence.
            if self._confidence_engine is not None:
                try:
                    comment.cfm_confidence = self._confidence_engine.score(
                        decision, evidence_graph
                    )
                except Exception as _cfm_exc:
                    logger.warning("cfm score failed for %s: %s", decision.decision_id, _cfm_exc)

            if evidence_graph.has_verified_evidence():
                # Distinguish blame-backed vs rule-backed vs generic support.
                has_rule = evidence_graph.subsystem_rule is not None
                verified_evs = [ev for ev in evidence_graph.evidence if ev.verified]
                all_blame = bool(verified_evs) and all(
                    ev.source_type == EvidenceSourceType.BLAME_HISTORY
                    for ev in verified_evs
                )
                if has_rule:
                    comment.evidence_status = "rule_backed"
                elif all_blame:
                    comment.evidence_status = "blame_backed"
                else:
                    comment.evidence_status = "supported"
                result.append(comment)
            else:
                # No verified evidence.
                is_safety_floor = (
                    comment.severity in (Severity.BLOCKER, Severity.WARNING)
                    and comment.confidence >= 0.70
                )
                if is_safety_floor:
                    comment.evidence_status = "safety_floored"
                    result.append(comment)
                else:
                    comment.evidence_status = "evidence_missing"
                    # evidence_missing non-floor comments are suppressed (not appended)
        return result, all_matched_series

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
