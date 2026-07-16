"""MAINTAINERS-file parsing for maintainer identification (Blueprint Sec. 21.3).

Given a kernel tree's ``MAINTAINERS`` file this builds a data-driven identity index
so the Lore Manager can mark ``ReviewComment.is_maintainer`` *robustly* and
deterministically (SPEC.md DoD: *extract_reviews sets is_maintainer from
MAINTAINERS*). Domain-agnostic: it reads only generic ``M:``/``R:`` person lines
and ``T:`` git-tree lines; it hardcodes no subsystem, name, or product.

Identity signals (all derived from the file, none hardcoded):
  * **emails** -- the ``M:``/``R:`` addresses (authoritative exact match).
  * **git usernames** -- the account segment of ``git.kernel.org`` ``T:`` tree URLs
    (e.g. ``krzk`` in ``.../git/krzk/linux.git``). A kernel.org git-tree account is
    a strong maintainer identity even when the person posts from an address that is
    not the one listed on the ``M:`` line.
  * **name -> domains** -- the set of email domains each display name uses in the
    file, used to *corroborate* a display-name match (so a name match from an
    unrelated address is rejected).

Rationale for the multi-signal approach (Gatekeeper HIGH finding): posting
addresses routinely differ from the ``M:`` address (e.g. a maintainer with a
kernel.org git tree posting as ``<user>@kernel.org``, or from a corporate mirror).
A bare display-name match is collision-prone -- any "Mark Brown" from any address
would be flagged -- so name matching is *gated* on a corroborating signal and is
never sufficient on its own.
"""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel, Field

# "M:\tName <email>" (maintainer) and "R:" (reviewer) lines carry a person.
_PERSON_RE = re.compile(r"^(?P<role>[MR]):\s*(?P<rest>.+)$")
_EMAIL_RE = re.compile(r"<([^>]+)>")
# "T:\tgit git://git.kernel.org/pub/scm/linux/kernel/git/<user>/<tree>.git"
_GIT_TREE_RE = re.compile(r"git\.kernel\.org/pub/scm/linux/kernel/git/(?P<user>[^/]+)/")

# git-tree accounts live on kernel.org; used to gate the strong git-username signal.
_GIT_ACCOUNT_DOMAIN = "kernel.org"


def _domain_of(email: str) -> str:
    return email.split("@", 1)[1].strip().lower() if "@" in email else ""


def _localpart_of(email: str) -> str:
    return email.split("@", 1)[0].strip().lower() if "@" in email else email.strip().lower()


class MaintainerIndex(BaseModel):
    """Data-driven index of maintainer/reviewer identities from a MAINTAINERS file."""

    emails: set[str] = Field(default_factory=set)          # lowercased addresses
    names: set[str] = Field(default_factory=set)           # lowercased display names
    git_usernames: set[str] = Field(default_factory=set)   # kernel.org git-tree accounts
    name_domains: dict[str, set[str]] = Field(default_factory=dict)  # name -> {domains}

    def is_maintainer(self, email: str | None, name: str | None = None) -> bool:
        """True iff the identity resolves to a known maintainer/reviewer.

        Matching (any of, in order of strength):
          1. exact posting-address match against an ``M:``/``R:`` email;
          2. a kernel.org git-tree account: domain is ``kernel.org`` and the
             local-part is a known git-tree username (catches e.g. ``krzk@kernel.org``
             when MAINTAINERS lists a different address for that person);
          3. a *corroborated* display-name match: the name is a known
             maintainer/reviewer AND the posting domain is one that name already
             uses in MAINTAINERS. A name match alone is never sufficient, so an
             unrelated ``Name <someone@elsewhere>`` is rejected -- even when the
             local-part happens to collide with a git-tree username.
        """
        em = email.strip().lower() if email else ""
        nm = name.strip().lower() if name else ""

        # 1. authoritative exact email match.
        if em and em in self.emails:
            return True

        local = _localpart_of(em) if em else ""
        domain = _domain_of(em) if em else ""

        # 2. kernel.org git-tree account identity.
        if domain == _GIT_ACCOUNT_DOMAIN and local and local in self.git_usernames:
            return True

        # 3. corroborated display-name match (never name-only): the posting
        #    address must use a domain the name already uses in MAINTAINERS.
        if nm and nm in self.names and domain and domain in self.name_domains.get(nm, set()):
            return True
        return False


def parse_maintainers(text: str) -> MaintainerIndex:
    """Parse MAINTAINERS file text into a :class:`MaintainerIndex`."""
    emails: set[str] = set()
    names: set[str] = set()
    git_usernames: set[str] = set()
    name_domains: dict[str, set[str]] = {}

    for line in text.splitlines():
        gt = _GIT_TREE_RE.search(line)
        if gt:
            git_usernames.add(gt.group("user").strip().lower())

        m = _PERSON_RE.match(line)
        if not m:
            continue
        rest = m.group("rest").strip()
        em = _EMAIL_RE.search(rest)
        if em:
            addr = em.group(1).strip().lower()
            emails.add(addr)
            name = rest[: em.start()].strip().lower()
            if name:
                names.add(name)
                dom = _domain_of(addr)
                if dom:
                    name_domains.setdefault(name, set()).add(dom)
        elif "@" in rest:
            emails.add(rest.split()[0].strip().lower())

    return MaintainerIndex(
        emails=emails,
        names=names,
        git_usernames=git_usernames,
        name_domains=name_domains,
    )


def load_maintainers(maintainers_path: str | Path) -> MaintainerIndex:
    """Load and parse a MAINTAINERS file from disk. Returns an empty index if
    the file is missing (degraded, never raises)."""
    path = Path(maintainers_path)
    if not path.exists():
        return MaintainerIndex()
    return parse_maintainers(path.read_text(encoding="utf-8", errors="replace"))
