"""WP-S1 extractors — pure diff-and-string analysis.

Every function here:
- takes a diff or series as input and returns a frozen collection,
- never performs I/O (no git, no network, no filesystem),
- is deterministic (Sec. 40: no time, no rng, no set-order dependency),
- short-circuits on binary patches / unparseable input rather than raising.

The extractors are intentionally regex-first: the goal is to recognise
kernel-conventional patterns (DT-binding YAML, C function definitions, etc.)
without pulling in a full parser. False negatives (missing a legit
declaration) are recoverable — the reducer simply doesn't suppress the
corresponding finding. False positives (declaring something the diff
doesn't introduce) are the risky direction and are guarded by requiring
addition-line ('+') prefixes and word boundaries.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kri.common.models import PatchSeries


# ---------------------------------------------------------------------------
# Public regexes (documented so tests can reason about matching)
# ---------------------------------------------------------------------------

# A DT compatible string: vendor,part-suffix. Vendor is [a-z0-9]+; part is
# [a-z0-9_-]+ possibly followed by more comma-separated pieces. Enforced
# lowercase to avoid matching macro tokens.
_COMPATIBLE_RE = re.compile(r"([a-z][a-z0-9-]*,[a-z][a-z0-9._-]*)")

# A DT property name line: two-space (or four-space) indent then ident colon.
# Matches "everest,jack-detect-inverted:" but not "compatible:" (identifiers
# without a vendor prefix are considered generic DT property names *only*
# when they appear under a properties: anchor).
_DT_PROP_KEY_RE = re.compile(r"^\+(\s{2,6})([a-z][a-z0-9,._-]+):\s*$")

# A C function definition on the added line: "+<retval> name(" at column 1
# (after the '+'). The retval token can be preceded by 'static', 'inline',
# 'const', 'void', a type-alias like 'int' / 'u32' / etc. — we don't try to
# validate the type, just capture the identifier before '('. Extended to
# also handle multi-line signatures where the '(' appears on the same line.
_C_FUNC_DEF_RE = re.compile(
    r"^\+(?:static\s+|inline\s+|const\s+|extern\s+)*"
    r"(?:struct\s+\w+\s*\*?\s*|"
    r"(?:unsigned\s+|signed\s+)?"
    r"(?:void|int|long|short|char|bool|u8|u16|u32|u64|s8|s16|s32|s64|size_t|ssize_t|"
    r"phys_addr_t|dma_addr_t|resource_size_t|loff_t|pid_t|uid_t|gid_t|"
    r"[A-Za-z_][\w]*_t)\s*\*?\s*)"
    r"([A-Za-z_][\w]*)\s*\("
)

# A struct or union type definition being introduced (with body '{').
_C_STRUCT_DEF_RE = re.compile(r"^\+\s*(?:struct|union)\s+([A-Za-z_][\w]*)\s*\{")

# A macro definition on an added line.
_C_MACRO_DEF_RE = re.compile(r"^\+#\s*define\s+([A-Z_][A-Z0-9_]*)")

# Diff file headers.
_DIFF_HEADER_RE = re.compile(r"^diff --git a/(\S+) b/(\S+)\s*$")
_NEW_FILE_MODE_RE = re.compile(r"^new file mode\b")

# Binary-diff markers.
_BINARY_MARKERS = ("GIT binary patch", "Binary files ")

# Subject index-parser.
_SUBJECT_INDEX_RE = re.compile(
    r"\[\s*(?:PATCH|RFC|PATCHv\d+|PATCH\s+v\d+|PATCH\s+RFC)[^]]*?"
    r"(\d+)\s*/\s*(\d+)\s*\]"
)


# ---------------------------------------------------------------------------
# Binary short-circuit
# ---------------------------------------------------------------------------


def is_binary_patch(diff: str) -> bool:
    """True when the diff appears to be a binary patch."""
    for marker in _BINARY_MARKERS:
        if marker in diff:
            return True
    return False


# ---------------------------------------------------------------------------
# DT / YAML extractors
# ---------------------------------------------------------------------------


def extract_compatibles(diff: str) -> set[str]:
    """Return every new compatible string added by ``diff``.

    Recognises two forms:
      1. Inline enum item under a DT-binding YAML enum:
             +          - thundercomm,qcs6490-rubikpi3-sndcard
      2. A device-tree source line under a compatible property:
             +    compatible = "thundercomm,qcs6490-rubikpi3-sndcard", ...

    Only additions are counted (lines with '+'). Removals / context are
    ignored so a rearranged file does not spuriously declare compatibles.
    """
    if is_binary_patch(diff):
        return set()

    out: set[str] = set()
    in_hunk = False
    for raw in diff.split("\n"):
        if raw.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk or not raw.startswith("+") or raw.startswith("+++"):
            continue
        # Strip the leading '+' and look for compatible tokens.
        line = raw[1:]
        # YAML enum-list item: "- vendor,part"
        m_list = re.match(r"\s*-\s*['\"]?([a-z][a-z0-9-]*,[a-z][a-z0-9._-]*)['\"]?\s*$", line)
        if m_list:
            out.add(m_list.group(1))
            continue
        # DTS compatible = "vendor,part", "vendor,part2";
        if "compatible" in line and "=" in line:
            for m in re.finditer(r'"([a-z][a-z0-9-]*,[a-z][a-z0-9._-]*)"', line):
                out.add(m.group(1))
            continue
        # Const-form YAML: "const: vendor,part"
        m_const = re.match(r"\s*const:\s*['\"]?([a-z][a-z0-9-]*,[a-z][a-z0-9._-]*)['\"]?\s*$", line)
        if m_const:
            out.add(m_const.group(1))
    return out


def extract_dt_properties(diff: str) -> set[str]:
    """Return every new top-level DT property name added under a
    ``properties:`` anchor.

    Distinguishes property additions from example / enum additions by
    tracking whether the current added-line block sits under a
    ``properties:`` anchor. A property line has form ``  name:`` at exactly
    two or four spaces of indent immediately after '+'.
    """
    if is_binary_patch(diff):
        return set()

    out: set[str] = set()
    in_hunk = False
    under_properties = False
    properties_indent: int | None = None

    for raw in diff.split("\n"):
        if raw.startswith("@@"):
            in_hunk = True
            under_properties = False
            properties_indent = None
            # If the hunk-header function-hint is 'properties:' we start the
            # hunk already under a properties anchor. YAML anchors sit at
            # column 0, so treat properties_indent as 0 in that case.
            tail = raw.split("@@", 2)
            if len(tail) >= 3 and tail[2].strip().rstrip(":") == "properties":
                under_properties = True
                properties_indent = 0
            continue
        if not in_hunk:
            continue
        # Consider both added and context lines for the properties: anchor,
        # because the anchor line itself may not be part of the diff hunk.
        body = raw[1:] if raw and raw[0] in "+- " else raw
        stripped = body.lstrip()
        indent = len(body) - len(stripped)
        if stripped.startswith("properties:"):
            under_properties = True
            properties_indent = indent
            continue
        # Leave properties: block if we hit a lower-indent non-empty line
        # that is a YAML key.
        if under_properties and stripped and not stripped.startswith("#"):
            if properties_indent is not None and indent <= properties_indent:
                # Same-or-lower indent than 'properties:' -> we've left it
                # UNLESS the current line is itself a two-level-deeper prop
                # key.  Simpler heuristic: only treat leaving as true when
                # the line ends with ':' at the properties-indent level and
                # is not a property child.
                if raw.startswith(("+", "-", " ")) and stripped.endswith(":") and indent == properties_indent:
                    under_properties = False
                    properties_indent = None
                    continue
        if not raw.startswith("+") or raw.startswith("+++"):
            continue
        if not under_properties:
            continue
        m = _DT_PROP_KEY_RE.match(raw)
        if not m:
            continue
        name = m.group(2)
        # Filter obvious non-property keys.
        if name in {"description", "type", "enum", "items", "const", "ref",
                    "$ref", "required", "properties", "additionalProperties",
                    "unevaluatedProperties", "examples", "minItems", "maxItems",
                    "maxLength", "minLength", "pattern", "oneOf", "anyOf",
                    "allOf"}:
            continue
        out.add(name)
    return out


# ---------------------------------------------------------------------------
# C extractors
# ---------------------------------------------------------------------------


def extract_c_symbols(diff: str) -> set[str]:
    """Return every C function / struct / macro *defined* by additions in
    the diff.

    Function-*declaration* additions in a header (a signature line ending in
    ``;``) also count, because they are useful for coupling detection.
    Static prototypes without a body are excluded — they are declarations
    within a translation unit and do not enlarge the API surface.
    """
    if is_binary_patch(diff):
        return set()

    out: set[str] = set()
    in_hunk = False
    for raw in diff.split("\n"):
        if raw.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk or not raw.startswith("+") or raw.startswith("+++"):
            continue
        line = raw  # keep '+' prefix for regex anchor
        m = _C_FUNC_DEF_RE.match(line)
        if m:
            name = m.group(1)
            if name not in {"if", "while", "for", "switch", "return",
                            "sizeof", "typeof", "__typeof__"}:
                out.add(name)
            continue
        m = _C_STRUCT_DEF_RE.match(line)
        if m:
            out.add(m.group(1))
            continue
        m = _C_MACRO_DEF_RE.match(line)
        if m:
            out.add(m.group(1))
    return out


def extract_added_files(diff: str) -> set[str]:
    """Return every path that genuinely appears as a new file in the diff.

    A path is considered new when its ``diff --git a/x b/y`` block is
    followed by a ``new file mode`` line before the first hunk header.
    """
    if is_binary_patch(diff):
        return set()

    out: set[str] = set()
    lines = diff.split("\n")
    i = 0
    n = len(lines)
    while i < n:
        m = _DIFF_HEADER_RE.match(lines[i])
        if not m:
            i += 1
            continue
        path = m.group(2)
        # Look ahead until the next diff header or first hunk for 'new file mode'.
        j = i + 1
        is_new = False
        while j < n:
            if lines[j].startswith("@@") or _DIFF_HEADER_RE.match(lines[j]):
                break
            if _NEW_FILE_MODE_RE.match(lines[j]):
                is_new = True
                break
            j += 1
        if is_new:
            out.add(path)
        i = j if j > i else i + 1
    return out


def extract_referenced_symbols(diff: str, symbols: set[str]) -> set[str]:
    """Return the subset of ``symbols`` that appear as identifiers in
    added lines of ``diff``.

    Word-boundary match only; substring collisions rejected. Used by
    R8 coupling annotation; the WP-S1A build does not consume this yet
    but the function is required for parity with the spec §2.2 and is
    tested here so it exists as a stable primitive.
    """
    if not symbols or is_binary_patch(diff):
        return set()
    # Compile a single alternation to keep this linear in the diff size.
    pattern = re.compile(r"\b(" + "|".join(re.escape(s) for s in sorted(symbols)) + r")\b")
    found: set[str] = set()
    in_hunk = False
    for raw in diff.split("\n"):
        if raw.startswith("@@"):
            in_hunk = True
            continue
        if not in_hunk or not raw.startswith("+") or raw.startswith("+++"):
            continue
        for m in pattern.finditer(raw):
            found.add(m.group(1))
    return found


def extract_containing_function(diff: str, target_line: int) -> str | None:
    """For an added-line ``target_line`` in the new-file side of ``diff``,
    return the C function name that contains it, or None.

    Used by the reducer's R5 function-scope dedup. Present in WP-S1A only
    as a documented primitive; it is unit-tested but the builder does not
    invoke it.
    """
    if is_binary_patch(diff):
        return None

    new_lineno = 0
    in_hunk = False
    current_fn: str | None = None

    for raw in diff.split("\n"):
        if raw.startswith("@@"):
            in_hunk = True
            # The @@ tail after the second '@@' may contain the enclosing
            # function name.
            tail = raw.split("@@", 2)
            if len(tail) >= 3:
                hint = tail[2].strip()
                m = re.match(r"[A-Za-z_][\w\s*,()]*\s([A-Za-z_]\w*)\s*\(", hint)
                if m:
                    current_fn = m.group(1)
            # Parse "@@ -x,y +new_start,new_count @@"
            plus_part = raw.split("+", 1)
            if len(plus_part) >= 2:
                num_part = plus_part[1].split(",")[0].split(" ")[0]
                try:
                    new_lineno = int(num_part) - 1
                except ValueError:
                    new_lineno = 0
            continue
        if not in_hunk:
            continue
        if raw.startswith("-") and not raw.startswith("---"):
            continue
        if raw.startswith("+++") or raw.startswith("---"):
            continue
        # Track function-definition adds/contexts inline.
        stripped = raw[1:] if raw and raw[0] in "+ " else raw
        m = _C_FUNC_DEF_RE.match("+" + stripped) if not raw.startswith("+") else _C_FUNC_DEF_RE.match(raw)
        if m:
            current_fn = m.group(1)
        if raw.startswith("+") or (raw.startswith(" ") and in_hunk):
            new_lineno += 1
            if new_lineno == target_line:
                return current_fn
    return current_fn if new_lineno >= target_line else None


# ---------------------------------------------------------------------------
# Series-level helpers
# ---------------------------------------------------------------------------


def parse_series_index(subject: str) -> tuple[int, int] | None:
    """Parse '[PATCH v2 3/6]' -> (3, 6). Return None when not present."""
    m = _SUBJECT_INDEX_RE.search(subject)
    if not m:
        return None
    try:
        return int(m.group(1)), int(m.group(2))
    except ValueError:
        return None


def extract_cover_letter(series: "PatchSeries") -> str | None:
    """Prefer ``series.cover_letter``; fall back to a sequence==0 patch's
    commit_message; return None otherwise. Never fetches externally.
    """
    if series.cover_letter:
        return series.cover_letter
    for p in series.patches:
        if p.sequence == 0 and p.commit_message:
            return p.commit_message
    return None
