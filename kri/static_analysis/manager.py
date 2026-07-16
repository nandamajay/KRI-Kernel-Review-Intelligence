"""Static Analysis Manager (Blueprint Sec. 21.6): checkpatch + degraded tools.

MVP scope (SPEC.md Sec. 9): a real ``checkpatch.pl`` runner producing normalized
:class:`StaticFinding` records ``{tool,file,line,category,severity,message}``.
``sparse``/``smatch``/``coccinelle`` are gated on tool availability and return a
structured *degraded* result (empty findings + a note) rather than crashing when
the binary is absent -- this is the explicitly-allowed degraded path.

Determinism: checkpatch output is parsed deterministically and findings are sorted
by (file, line, category). No wall-clock or RNG. Domain-agnostic: the manager only
knows about patches, files, and tool output -- no subsystem identifiers.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

# checkpatch level -> KRI severity (Blueprint Sec. 7 layer-1 structural).
_CHECKPATCH_SEVERITY = {"ERROR": "blocker", "WARNING": "warning", "CHECK": "info"}

# Non-terse checkpatch block header, e.g. "ERROR: spaces required around ...".
_LEVEL_RE = re.compile(r"^(?P<level>ERROR|WARNING|CHECK):\s*(?P<msg>.*)$")
# "#123: FILE: path/to/file.c:45:"  (the FILE:line locator line).
_FILE_RE = re.compile(r"^#\d+:\s*FILE:\s*(?P<file>[^:]+):(?P<line>\d+):")
# Terse fallback, e.g. "/tmp/x.diff:8: ERROR: spaces required ...".
_TERSE_RE = re.compile(
    r"^(?P<src>.+?):(?P<line>\d+):\s*(?P<level>ERROR|WARNING|CHECK):\s*(?P<msg>.*)$"
)


class StaticAnalysisConfig:
    """Configuration for :class:`StaticAnalysisManagerImpl`."""

    def __init__(
        self,
        repo_path: str | Path,
        checkpatch_path: str | Path | None = None,
        perl: str = "perl",
        timeout: int = 120,
    ) -> None:
        self.repo_path = Path(repo_path)
        self.checkpatch_path = (
            Path(checkpatch_path)
            if checkpatch_path
            else self.repo_path / "scripts" / "checkpatch.pl"
        )
        self.perl = perl
        self.timeout = timeout


class StaticAnalysisManagerImpl:
    """Concrete :class:`kri.common.interfaces.StaticAnalysisManager`."""

    def __init__(self, config: StaticAnalysisConfig) -> None:
        self._cfg = config

    # -- interface: run_checkpatch ------------------------------------------
    def run_checkpatch(self, patch: Any) -> list[dict[str, Any]]:
        """Run checkpatch.pl against a patch's diff; return normalized findings.

        If checkpatch.pl is unavailable, returns a single degraded finding noting
        the missing tool (never raises)."""
        diff = getattr(patch, "diff", "") or ""
        patch_id = getattr(patch, "patch_id", None)
        if not self._cfg.checkpatch_path.exists():
            return [self._degraded("checkpatch", "checkpatch.pl not found in tree")]
        if not diff.strip():
            return []

        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as fh:
            if not diff.endswith("\n"):
                diff += "\n"
            fh.write(diff)
            tmp = fh.name
        try:
            proc = subprocess.run(
                [
                    self._cfg.perl, str(self._cfg.checkpatch_path),
                    "--no-tree", "--no-summary", "--show-types", tmp,
                ],
                capture_output=True, text=True, timeout=self._cfg.timeout,
                cwd=str(self._cfg.repo_path),
            )
            findings = self._parse_checkpatch(proc.stdout, patch_id=patch_id)
        except (subprocess.TimeoutExpired, OSError) as exc:
            return [self._degraded("checkpatch", f"checkpatch execution failed: {exc}")]
        finally:
            Path(tmp).unlink(missing_ok=True)
        return self._sort(findings)

    def _parse_checkpatch(
        self, output: str, patch_id: str | None = None
    ) -> list[dict[str, Any]]:
        """Parse non-terse checkpatch output into StaticFinding dicts.

        Non-terse format emits blocks::

            <LEVEL>:<TYPE>: message
            #<n>: FILE: <file>:<line>:
            <context lines>

        We pair each level line with the following ``FILE:`` locator. Falls back to
        the terse ``src:line: LEVEL: msg`` form if present."""
        findings: list[dict[str, Any]] = []
        lines = output.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            m = _LEVEL_RE.match(line)
            if m:
                level = m.group("level")
                message = m.group("msg").strip()
                category = "style"
                # checkpatch --show-types puts the type after the level: "LEVEL:TYPE:"
                type_m = re.match(r"^[A-Z]+:(?P<type>[A-Z0-9_]+):\s*(?P<msg>.*)$", line)
                if type_m:
                    category = type_m.group("type").lower()
                    message = type_m.group("msg").strip()
                file_path = None
                line_no = 0
                # Look ahead a few lines for the FILE: locator.
                for j in range(i + 1, min(i + 4, len(lines))):
                    fm = _FILE_RE.match(lines[j])
                    if fm:
                        file_path = fm.group("file").strip()
                        line_no = int(fm.group("line"))
                        break
                findings.append(self._finding(
                    tool="checkpatch", file=file_path, line=line_no,
                    category=category, severity=_CHECKPATCH_SEVERITY[level],
                    message=message, patch_id=patch_id,
                ))
                i += 1
                continue
            # Terse fallback.
            tm = _TERSE_RE.match(line)
            if tm:
                findings.append(self._finding(
                    tool="checkpatch", file=None, line=int(tm.group("line")),
                    category="style", severity=_CHECKPATCH_SEVERITY[tm.group("level")],
                    message=tm.group("msg").strip(), patch_id=patch_id,
                ))
            i += 1
        return findings

    # -- interface: run_sparse / smatch / coccinelle (degraded in MVP) ------
    def run_sparse(self, files: list[str]) -> list[dict[str, Any]]:
        """Run sparse over ``files``; degraded no-op if the binary is absent."""
        if shutil.which("sparse") is None:
            return [self._degraded("sparse", "sparse binary not installed")]
        # Sparse requires a configured/built tree; MVP records availability only.
        return [self._degraded(
            "sparse", "sparse present but full build integration is post-MVP")]

    def run_smatch(self, files: list[str]) -> list[dict[str, Any]]:
        """Run smatch over ``files``; degraded no-op if the binary is absent."""
        if shutil.which("smatch") is None:
            return [self._degraded("smatch", "smatch not available in MVP")]
        return [self._degraded("smatch", "smatch present but integration is post-MVP")]

    def run_coccinelle(
        self, files: list[str], scripts: list[str]
    ) -> list[dict[str, Any]]:
        """Run coccinelle semantic patches; degraded no-op if absent."""
        if shutil.which("spatch") is None:
            return [self._degraded("coccinelle", "coccinelle (spatch) not available in MVP")]
        return [self._degraded(
            "coccinelle", "coccinelle present but integration is post-MVP")]

    # -- interface: normalize -----------------------------------------------
    def normalize(self, output: Any) -> list[dict[str, Any]]:
        """Normalize arbitrary raw tool output into sorted StaticFinding dicts.

        Accepts already-normalized dicts (validated/back-filled) or a raw checkpatch
        stdout string. Missing keys are filled with conservative defaults."""
        if isinstance(output, str):
            return self._sort(self._parse_checkpatch(output))
        if isinstance(output, dict):
            output = [output]
        findings: list[dict[str, Any]] = []
        for item in output or []:
            if not isinstance(item, dict):
                continue
            findings.append(self._finding(
                tool=str(item.get("tool", "unknown")),
                file=item.get("file"),
                line=int(item.get("line", 0) or 0),
                category=str(item.get("category", "")),
                severity=str(item.get("severity", "info")),
                message=str(item.get("message", "")),
                patch_id=item.get("patch_id"),
                degraded=bool(item.get("degraded", False)),
            ))
        return self._sort(findings)

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def _finding(
        tool: str, file: str | None, line: int, category: str, severity: str,
        message: str, patch_id: str | None = None, degraded: bool = False,
    ) -> dict[str, Any]:
        return {
            "tool": tool,
            "file": file,
            "line": line,
            "category": category,
            "severity": severity,
            "message": message,
            "patch_id": patch_id,
            "degraded": degraded,
        }

    def _degraded(self, tool: str, reason: str) -> dict[str, Any]:
        return self._finding(
            tool=tool, file=None, line=0, category="tool_unavailable",
            severity="info", message=reason, degraded=True,
        )

    @staticmethod
    def _sort(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return sorted(
            findings,
            key=lambda f: (f.get("file") or "", int(f.get("line") or 0),
                           f.get("category") or "", f.get("message") or ""),
        )
