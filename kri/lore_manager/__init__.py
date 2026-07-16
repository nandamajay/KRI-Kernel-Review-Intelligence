"""KRI Lore Manager (Blueprint Sec. 21.3).

Fetch, cache, and structure lore.kernel.org threads into domain-agnostic
:class:`Thread`/:class:`Message` objects and :class:`ReviewComment`s with
provenance. See :mod:`kri.lore_manager.manager`.
"""

from __future__ import annotations

from .maintainers import MaintainerIndex, load_maintainers, parse_maintainers
from .manager import LoreConfig, LoreManagerImpl, LoreOfflineError
from .mbox import (
    Message,
    SubjectInfo,
    Thread,
    files_from_diff,
    parse_mbox_bytes,
    parse_mbox_gz,
    parse_subject,
    split_commit_message_and_diff,
    strip_message_id,
)

__all__ = [
    "LoreConfig",
    "LoreManagerImpl",
    "LoreOfflineError",
    "MaintainerIndex",
    "load_maintainers",
    "parse_maintainers",
    "Message",
    "SubjectInfo",
    "Thread",
    "parse_mbox_bytes",
    "parse_mbox_gz",
    "parse_subject",
    "split_commit_message_and_diff",
    "files_from_diff",
    "strip_message_id",
]
