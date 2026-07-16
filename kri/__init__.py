"""KRI — Kernel Review Intelligence.

Evidence-backed simulation of Linux kernel maintainer patch review.

This top-level package is the **Generic Runtime**: it is domain-agnostic and MUST
contain no domain-specific references (a subsystem's C symbol prefix, source path,
or product name) per Domain Isolation (Constitution Sec. 9). Domain behavior is
supplied by pluggable Domain Knowledge Packages under
``kri.packages.<domain>`` discovered via the ``kri.dkp`` entry-point group.
"""

__version__ = "0.1.0"
