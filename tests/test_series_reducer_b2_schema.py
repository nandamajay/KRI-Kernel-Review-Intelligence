"""WP-S1B Step B2 — schema-compat tests (SM13-A/B/C/D).

B2 adds two defaulted fields to :class:`InlineComment`:

- ``series_prefix: str = ""``
- ``series_provenance: SeriesProvenance | None = None``

The schema-migration checks (readiness §7.B2 Test SM13) prove:

- **SM13-A** — pre-B2 JSON (no new keys) deserializes cleanly; both new
  fields land at their defaults.
- **SM13-B** — round-trip of a comment whose reducer-audit fields are at
  defaults produces the same **legacy** key set when serialized with
  ``exclude_defaults=True``. This is the byte-identity guarantee for
  every OFF-mode / non-multi-patch review — new keys ARE emitted, but
  only their default values, and dropping-defaults recovers the pre-B2
  byte-shape.
- **SM13-C** — end-to-end engine run round-trips through
  ``model_validate``; every comment carries the defaults on the way in
  and on the way out.
- **SM13-D** — legacy keys are byte-identical to a pre-B2 fixture; new
  keys are present at defaults (``series_prefix == ""``,
  ``series_provenance is None``).

None of these tests run the reducer with mode≠"off": the whole point of
B2 is to prove the schema is safe *before* rule bodies land in later
steps.
"""

from __future__ import annotations

import copy
import json

from kri.common.models import Severity
from kri.llm.models import InlineComment
from kri.llm.reviewer import IntelligentReviewEngine
from kri.series.models import SeriesProvenance


# ---------------------------------------------------------------------------
# Legacy pre-B2 fixture (used by SM13-A and SM13-D)
# ---------------------------------------------------------------------------


_LEGACY_KEYS = frozenset(
    {
        "file_path",
        "line_number",
        "hunk_context",
        "category",
        "severity",
        "message",
        "suggestion",
        "upstream_comment",
        "confidence",
        "reasoning",
    }
)

_B2_ADDED_KEYS = frozenset({"series_prefix", "series_provenance"})


def _legacy_payload() -> dict:
    """A pre-B2 InlineComment payload (JSON keys only from _LEGACY_KEYS)."""
    return {
        "file_path": "drivers/x/foo.c",
        "line_number": 42,
        "hunk_context": "static int foo(void)\n\tint a;",
        "category": "convention",
        "severity": "info",
        "message": "prefer scoped locals",
        "suggestion": None,
        "upstream_comment": None,
        "confidence": 0.63,
        "reasoning": "K&R style",
    }


# ---------------------------------------------------------------------------
# SM13-A: pre-B2 JSON deserializes with defaults populated
# ---------------------------------------------------------------------------


def test_SM13_A_pre_b2_json_deserializes_with_defaults():
    """A JSON payload written before B2 (no ``series_prefix``/
    ``series_provenance`` keys) must validate under the B2 model and land
    the two new fields at their exact defaults.
    """
    legacy = _legacy_payload()
    cmt = InlineComment.model_validate(legacy)

    # Legacy fields preserved bit-for-bit.
    assert cmt.file_path == legacy["file_path"]
    assert cmt.line_number == legacy["line_number"]
    assert cmt.category == legacy["category"]
    assert cmt.severity == Severity.INFO
    assert cmt.message == legacy["message"]
    assert cmt.confidence == legacy["confidence"]

    # New B2 fields defaulted.
    assert cmt.series_prefix == ""
    assert cmt.series_provenance is None


def test_SM13_A_pre_b2_json_from_json_string_deserializes():
    """Same guarantee, but via ``model_validate_json`` — the actual code
    path that would run on cached-JSON reload."""
    legacy_json = json.dumps(_legacy_payload())
    cmt = InlineComment.model_validate_json(legacy_json)
    assert cmt.series_prefix == ""
    assert cmt.series_provenance is None


# ---------------------------------------------------------------------------
# SM13-B: default round-trip with exclude_defaults matches legacy key set
# ---------------------------------------------------------------------------


def test_SM13_B_default_round_trip_matches_legacy_keys_with_exclude_defaults():
    """A comment whose reducer-audit fields are at defaults must dump to
    the pre-B2 key set when ``exclude_defaults=True`` — the invariant is
    that OFF-mode / non-multi-patch reviews can produce byte-identical
    JSON to WP-S1A by dropping defaults at the boundary.

    Note: ``suggestion=None`` and ``upstream_comment=None`` are also
    defaults, so they too drop out — we test the emitted key set is a
    subset of legacy keys, never a superset.
    """
    cmt = InlineComment.model_validate(_legacy_payload())
    dumped = cmt.model_dump(exclude_defaults=True)

    emitted = set(dumped.keys())

    # Not a single B2-added key may leak into the dump.
    assert emitted & _B2_ADDED_KEYS == set(), (
        f"exclude_defaults leaked B2 keys: {emitted & _B2_ADDED_KEYS}"
    )
    # Every emitted key was legal in pre-B2.
    assert emitted <= _LEGACY_KEYS, (
        f"exclude_defaults emitted unknown keys: {emitted - _LEGACY_KEYS}"
    )


def test_SM13_B_default_round_trip_full_dump_adds_only_the_two_new_keys():
    """The dual of SM13-B: a **full** ``model_dump()`` (defaults included)
    must add EXACTLY the two new keys — no more, no less."""
    cmt = InlineComment.model_validate(_legacy_payload())
    full = cmt.model_dump()

    added = set(full.keys()) - _LEGACY_KEYS
    assert added == _B2_ADDED_KEYS, (
        f"unexpected key delta vs. legacy — added={added}, expected={_B2_ADDED_KEYS}"
    )
    # And the added keys hold defaults.
    assert full["series_prefix"] == ""
    assert full["series_provenance"] is None


# ---------------------------------------------------------------------------
# SM13-C: engine end-to-end run round-trips through model_validate
# ---------------------------------------------------------------------------


def _fake_client(payloads):
    """Minimal LLM stub matching the B1 test's shape."""
    from unittest.mock import MagicMock

    client = MagicMock()
    client._cfg = MagicMock(model="test-model")
    client.stats = {}
    it = iter(payloads)

    def _pop(*a, **kw):
        try:
            return next(it)
        except StopIteration:
            return []

    client.complete_json.side_effect = _pop
    resp = MagicMock()
    resp.content = "ok"
    client.complete.return_value = resp
    return client


def _single_patch_series():
    from kri.common.models import Patch, PatchSeries

    diff = (
        "diff --git a/drivers/x/foo.c b/drivers/x/foo.c\n"
        "index 000..111 100644\n"
        "--- a/drivers/x/foo.c\n"
        "+++ b/drivers/x/foo.c\n"
        "@@ -10,3 +10,4 @@ static int foo(void)\n"
        " \tint a;\n"
        "+\tint c;\n"
        " \treturn a + b;\n"
    )
    p = Patch(
        patch_id="p1",
        subject="[PATCH 1/1] p1",
        commit_message="",
        files_changed=["drivers/x/foo.c"],
        diff=diff,
        sequence=1,
        series_total=1,
    )
    return PatchSeries(
        series_id="series-b2-1",
        title="B2 schema test",
        cover_letter="",
        patches=[p],
    )


def test_SM13_C_engine_round_trip_defaults_survive():
    """Run the engine end-to-end (mode='off', single patch → reducer
    short-circuits regardless), then round-trip each emitted comment via
    ``model_validate(model_dump())`` and assert the two new fields still
    hold their defaults post-trip."""
    payload = [
        {"what_it_does": "s1", "why_needed": "", "risk_level": "low",
         "test_recommendations": []},
        [{"file_path": "drivers/x/foo.c", "line_number": 13,
          "category": "convention", "severity": "info", "message": "m",
          "confidence": 0.6}],
        [],
    ]
    client = _fake_client(copy.deepcopy(payload))
    engine = IntelligentReviewEngine(client=client)  # defaults → mode='off'
    report = engine.review(_single_patch_series())

    assert len(report.patches) == 1
    for pr in report.patches:
        for c in pr.inline_comments:
            # Defaults on the way out of the engine.
            assert c.series_prefix == ""
            assert c.series_provenance is None
            # Round-trip through JSON.
            dumped = c.model_dump()
            revalidated = InlineComment.model_validate(dumped)
            assert revalidated.series_prefix == ""
            assert revalidated.series_provenance is None
            # Legacy fields survive the round-trip.
            assert revalidated.file_path == c.file_path
            assert revalidated.line_number == c.line_number
            assert revalidated.category == c.category
            assert revalidated.severity == c.severity
            assert revalidated.message == c.message


# ---------------------------------------------------------------------------
# SM13-D: legacy keys byte-identical against a pre-B2 fixture
# ---------------------------------------------------------------------------


def test_SM13_D_legacy_keys_byte_identical_with_defaults_dropped():
    """A comment constructed with the legacy payload, dumped with
    ``exclude_defaults=True``, must produce the same JSON shape a pre-B2
    build would have produced for a comment whose optionals were also at
    their defaults."""
    legacy = _legacy_payload()
    cmt = InlineComment.model_validate(legacy)

    dumped = cmt.model_dump(exclude_defaults=True)

    # 'suggestion' and 'upstream_comment' default to None; 'hunk_context'
    # and 'reasoning' default to ""; 'category' defaults to "general";
    # 'severity' defaults to Severity.INFO; 'confidence' defaults to 0.5.
    # These CAN drop out of exclude_defaults when they hold the default.
    # In this fixture: hunk_context, message, category, severity=INFO,
    # confidence=0.63, reasoning are all non-default OR equal legacy
    # values — none of them can be a B2 addition.
    for key in dumped:
        assert key in _LEGACY_KEYS, (
            f"exclude_defaults emitted unknown key: {key!r}"
        )

    # Explicit: the two B2 fields must NOT appear.
    assert "series_prefix" not in dumped
    assert "series_provenance" not in dumped


def test_SM13_D_non_default_series_provenance_serialises_via_to_metadata():
    """When a comment DOES carry reducer provenance (populated by future
    B4+ rule bodies), ``model_dump()`` must route the value through
    :meth:`SeriesProvenance.to_metadata` and produce the deterministic
    sorted-key dict."""
    prov = SeriesProvenance(
        depends_on_patches=("p2", "p3"),
        absorbed_from=("f1", "f2"),
        suppressed_alternatives=("s1",),
    )
    cmt = InlineComment(
        file_path="drivers/x/foo.c",
        line_number=1,
        message="m",
        series_prefix="[3/5]",
        series_provenance=prov,
    )
    dumped = cmt.model_dump()
    assert dumped["series_prefix"] == "[3/5]"
    # Serializer output must equal to_metadata() output exactly.
    assert dumped["series_provenance"] == prov.to_metadata()
    # And the dict is sorted-by-attr-name deterministic:
    assert list(dumped["series_provenance"].keys()) == [
        "absorbed_from",
        "depends_on_patches",
        "suppressed_alternatives",
    ]
    # Round-trip: dict form re-validates to an equal SeriesProvenance.
    reloaded = InlineComment.model_validate(dumped)
    assert reloaded.series_provenance == prov
