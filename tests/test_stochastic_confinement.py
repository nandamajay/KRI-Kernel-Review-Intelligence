"""Constitution Sec. 40 ("Stochastic Confinement") enforcement.

C-40: no RNG / model nondeterminism outside ``kri/learning/``. Every other
module's output must be a pure function of its inputs so that review results,
reports, and benchmarks are byte-reproducible.

This module AST-walks every ``.py`` file under ``kri/`` except
``kri/learning/**`` and fails if it finds an import or call from a fixed
denylist of nondeterminism sources: ``random``/``secrets``, ``time.time``/
``time_ns``/``monotonic``/``perf_counter``, ``datetime.now``/``utcnow``/
``date.today``, ``uuid.uuid1``/``uuid4``, ``numpy.random.*``, ``os.urandom``.

A small, explicit, per-line allowlist covers call sites that use one of these
APIs but do not feed the value into any Decision/Confidence/Report reasoning
output -- see ``_ALLOWED_CALLS`` below for the justification of each entry.
"""

from __future__ import annotations

import ast
from pathlib import Path

KRI_ROOT = Path(__file__).parent.parent / "kri"

_DENYLIST_IMPORT_MODULES = {"random", "secrets"}

# (module/object path, attribute name) pairs that are nondeterministic.
_DENYLIST_CALLS = {
    ("time", "time"),
    ("time", "time_ns"),
    ("time", "monotonic"),
    ("time", "monotonic_ns"),
    ("time", "perf_counter"),
    ("time", "perf_counter_ns"),
    ("datetime", "now"),
    ("datetime", "utcnow"),
    ("date", "today"),
    ("uuid", "uuid1"),
    ("uuid", "uuid4"),
    ("random", "*"),
    ("secrets", "*"),
    ("os", "urandom"),
}

# file (relative to kri/) -> set of line numbers that are known, justified
# uses of a denylisted call which never feed a Decision/Confidence/Report
# reasoning output. Each entry is pure elapsed-time telemetry/rate-limiting,
# or is structurally excluded from every cached/test/benchmark replay path.
# Do NOT add an entry here without the same justification.
_ALLOWED_CALLS: dict[str, set[int]] = {
    # Elapsed-wall-clock telemetry only: feeds IntelligentReport.metadata
    # ["processing_time_seconds"], which is never read by any
    # Decision/Confidence/Report computation or asserted on by any test.
    "llm/reviewer.py": {59, 75},
    # time.monotonic(): pure live-network rate-limiting delay, has zero
    # effect on parsed thread/patch content.
    # datetime.now(): retrieved_at is set ONLY on a genuine cache-miss live
    # fetch; load_cached() (the only path used by tests/benchmarks/replay)
    # instead derives retrieved_at from the deterministic file mtime, and
    # ReviewComment construction explicitly passes retrieved_at=None with
    # the comment "kept null: retrieval time must not affect output"
    # (see tests/test_lore_manager.py::test_... asserting retrieved_at is None).
    "lore_manager/manager.py": {115, 118, 150},
}


def _iter_kri_files() -> list[Path]:
    return sorted(
        p
        for p in KRI_ROOT.rglob("*.py")
        if "learning" not in p.relative_to(KRI_ROOT).parts
        and "__pycache__" not in p.parts
    )


def _resolve_attr_chain(node: ast.expr) -> tuple[str, str] | None:
    """For an ``a.b`` / ``a.b.c`` attribute-access expression used as a call
    target, return (base_name, attr_name) if the base is a plain Name."""
    if not isinstance(node, ast.Attribute):
        return None
    base = node.value
    if isinstance(base, ast.Name):
        return (base.id, node.attr)
    return None


def _scan_file(path: Path) -> list[tuple[int, str]]:
    """Return a list of (lineno, description) violations in ``path``."""
    tree = ast.parse(path.read_text(), filename=str(path))
    rel = str(path.relative_to(KRI_ROOT))
    allowed_lines = _ALLOWED_CALLS.get(rel, set())
    violations: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                top = alias.name.split(".")[0]
                if top in _DENYLIST_IMPORT_MODULES and node.lineno not in allowed_lines:
                    violations.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            top = module.split(".")[0]
            if top in _DENYLIST_IMPORT_MODULES and node.lineno not in allowed_lines:
                violations.append((node.lineno, f"from {module} import ..."))
        elif isinstance(node, ast.Call):
            resolved = _resolve_attr_chain(node.func)
            if resolved is None:
                continue
            base, attr = resolved
            hit = (base, attr) in _DENYLIST_CALLS or (base, "*") in _DENYLIST_CALLS
            if hit and node.lineno not in allowed_lines:
                violations.append((node.lineno, f"{base}.{attr}(...)"))

    return violations


def test_no_nondeterminism_outside_learning() -> None:
    """Constitution Sec. 40: no RNG/model nondeterminism outside kri/learning/."""
    all_violations: list[str] = []
    for path in _iter_kri_files():
        for lineno, desc in _scan_file(path):
            rel = path.relative_to(KRI_ROOT)
            all_violations.append(f"{rel}:{lineno}: {desc}")

    assert not all_violations, (
        "Constitution Sec. 40 violation(s): nondeterministic call(s)/import(s) "
        "found outside kri/learning/ with no allowlist justification:\n"
        + "\n".join(all_violations)
    )


def test_detector_catches_a_real_violation(tmp_path) -> None:
    """Self-test: prove the AST walker actually flags a genuine violation,
    since the current codebase has none left to catch (C-40 already holds)."""
    bad_file = tmp_path / "offender.py"
    bad_file.write_text(
        "import random\n"
        "import time\n"
        "\n"
        "def pick():\n"
        "    return random.choice([1, 2, 3])\n"
        "\n"
        "def stamp():\n"
        "    return time.time()\n"
    )

    tree = ast.parse(bad_file.read_text(), filename=str(bad_file))
    violations: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in _DENYLIST_IMPORT_MODULES:
                    violations.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.Call):
            resolved = _resolve_attr_chain(node.func)
            if resolved is None:
                continue
            base, attr = resolved
            if (base, attr) in _DENYLIST_CALLS or (base, "*") in _DENYLIST_CALLS:
                violations.append((node.lineno, f"{base}.{attr}(...)"))

    assert violations, "detector failed to catch a synthetic real violation"
    assert any("random" in desc for _, desc in violations)
    assert any("time.time" in desc for _, desc in violations)


def test_allowlist_entries_still_point_at_denylisted_calls() -> None:
    """Guard against a stale allowlist: every allowed line must still contain
    a call this test would otherwise flag, so the allowlist can't silently
    grow to cover lines that no longer need an exception."""
    for rel, lines in _ALLOWED_CALLS.items():
        path = KRI_ROOT / rel
        assert path.exists(), f"allowlisted file {rel} no longer exists"
        tree = ast.parse(path.read_text(), filename=str(path))
        found_lines: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call):
                resolved = _resolve_attr_chain(node.func)
                if resolved is None:
                    continue
                base, attr = resolved
                if (base, attr) in _DENYLIST_CALLS or (base, "*") in _DENYLIST_CALLS:
                    found_lines.add(node.lineno)
        stale = lines - found_lines
        assert not stale, (
            f"{rel}: allowlist lines {stale} no longer contain a denylisted "
            "call -- remove the stale allowlist entry"
        )
