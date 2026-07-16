"""Kernel-version parsing/ordering helpers for the EKG (Blueprint Sec. 8.4).

Domain-agnostic: this only understands the generic ``x.y[.z][-rcN]`` kernel
version grammar and the temporal-validity comparison used by the Knowledge Graph.
No wall-clock, no RNG — parsing is a pure function of its input string
(Constitution Sec. 31).
"""

from __future__ import annotations

import re

from kri.common.models import KernelVersion, VersionRange

_VERSION_RE = re.compile(
    r"^\s*v?(?P<major>\d+)\.(?P<minor>\d+)(?:\.(?P<patch>\d+))?"
    r"(?:[-.]?rc(?P<rc>\d+))?"
)

# +inf sentinel for open-ended (valid_until=None) ranges.
_INF_SORT_KEY = (10**9, 0, 0, 0)


def parse_kernel_version(raw: str) -> KernelVersion:
    """Parse ``"6.9-rc1"`` / ``"6.6"`` / ``"5.15.0"`` into a :class:`KernelVersion`.

    Raises :class:`ValueError` on an unparseable string so callers fail loudly
    rather than silently mis-ordering the temporal graph.
    """
    m = _VERSION_RE.match(raw)
    if not m:
        raise ValueError(f"unparseable kernel version: {raw!r}")
    return KernelVersion(
        raw=raw.strip(),
        major=int(m.group("major")),
        minor=int(m.group("minor")),
        patch=int(m.group("patch") or 0),
        rc=int(m.group("rc")) if m.group("rc") is not None else None,
    )


def coerce_version(value: KernelVersion | str) -> KernelVersion:
    """Accept either a :class:`KernelVersion` or a raw string."""
    if isinstance(value, KernelVersion):
        return value
    return parse_kernel_version(value)


def make_range(
    valid_from: KernelVersion | str,
    valid_until: KernelVersion | str | None = None,
) -> VersionRange:
    """Build a :class:`VersionRange` from versions or raw strings (``None``==HEAD)."""
    vf = coerce_version(valid_from)
    vu = coerce_version(valid_until) if valid_until is not None else None
    return VersionRange(valid_from=vf, valid_until=vu)


def range_contains(vr: VersionRange | None, as_of: KernelVersion) -> bool:
    """Half-open temporal test (Blueprint Sec. 8.4 / SPEC §4.4).

    Visible iff ``valid_from <= as_of < (valid_until or +inf)`` by ``sort_key()``.
    A ``None`` range is treated as "always valid" (HEAD-scoped seed data).
    """
    if vr is None:
        return True
    lo = vr.valid_from.sort_key()
    hi = vr.valid_until.sort_key() if vr.valid_until is not None else _INF_SORT_KEY
    return lo <= as_of.sort_key() < hi
