"""Constitution Sec. 32 ("Frozen Core") enforcement.

Public members of ``kri.common.models`` and ``kri.common.interfaces`` are part
of the frozen core architecture: any signature drift must be a deliberate,
reviewed decision, not a silent side effect of an unrelated change.

This module computes a deterministic text signature of every public class,
Protocol, and enum in those two modules and compares it against a committed
fixture. A mismatch fails the test with a unified diff and the exact command
to regenerate the fixture when the drift is intentional.
"""

from __future__ import annotations

import inspect
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

import kri.common.interfaces as interfaces_module
import kri.common.models as models_module

FIXTURE_PATH = Path(__file__).parent / "fixtures" / "frozen_core_signature.txt"

_ACCEPT_ENV_VAR = "KRI_ACCEPT_FROZEN_CORE_DRIFT"


def _is_protocol(obj: object) -> bool:
    return inspect.isclass(obj) and getattr(obj, "_is_protocol", False)


def _enum_signature(cls: type[Enum]) -> str:
    members = sorted((m.name, str(m.value)) for m in cls)
    body = "\n".join(f"    {name} = {value!r}" for name, value in members)
    return f"enum {cls.__name__}:\n{body}"


def _pydantic_signature(cls: type[BaseModel]) -> str:
    lines: list[str] = []
    for field_name in sorted(cls.model_fields):
        field = cls.model_fields[field_name]
        annotation = _format_annotation(field.annotation)
        required = field.is_required()
        lines.append(f"    {field_name}: {annotation} (required={required})")
    # Only methods defined directly on this class (not inherited pydantic
    # boilerplate like model_dump/copy/json) -- keeps the signature stable
    # across pydantic versions while still catching real API changes.
    methods = sorted(
        name
        for name, member in vars(cls).items()
        if not name.startswith("_") and inspect.isfunction(member)
    )
    for name in methods:
        sig = inspect.signature(getattr(cls, name))
        lines.append(f"    def {name}{sig}")
    body = "\n".join(lines)
    return f"model {cls.__name__}:\n{body}"


def _protocol_signature(cls: type) -> str:
    lines: list[str] = []
    members = inspect.getmembers(cls)
    method_names = sorted(
        name
        for name, member in members
        if not name.startswith("_")
        and (inspect.isfunction(member) or inspect.ismethod(member))
    )
    for name in method_names:
        sig = inspect.signature(getattr(cls, name))
        lines.append(f"    def {name}{sig}")
    property_names = sorted(
        name for name, member in members if isinstance(member, property)
    )
    for name in property_names:
        lines.append(f"    property {name}")
    body = "\n".join(lines)
    return f"protocol {cls.__name__}:\n{body}"


def _format_annotation(annotation: object) -> str:
    return str(annotation).replace(" ", "")


def _other_signature(name: str, obj: object) -> str:
    return f"value {name} = {obj!r}"


def _member_signature(name: str, obj: object) -> str:
    if inspect.isclass(obj) and issubclass(obj, Enum):
        return _enum_signature(obj)
    if inspect.isclass(obj) and issubclass(obj, BaseModel):
        return _pydantic_signature(obj)
    if _is_protocol(obj):
        return _protocol_signature(obj)
    return _other_signature(name, obj)


def _module_signature(module: object) -> list[str]:
    public_names = sorted(getattr(module, "__all__", []))
    return [_member_signature(name, getattr(module, name)) for name in public_names]


def compute_frozen_core_signature() -> str:
    """Deterministic, sorted, human-diffable text blob of the frozen-core public API."""
    blocks: list[str] = []
    blocks.append("=== kri.common.models ===")
    blocks.extend(_module_signature(models_module))
    blocks.append("=== kri.common.interfaces ===")
    blocks.extend(_module_signature(interfaces_module))
    return "\n\n".join(blocks) + "\n"


def test_frozen_core_fixture_exists() -> None:
    """Guard against silent deletion of the frozen-core baseline (Constitution Sec. 32)."""
    assert FIXTURE_PATH.exists(), (
        f"{FIXTURE_PATH} is missing. Regenerate it with: "
        f"KRI_ACCEPT_FROZEN_CORE_DRIFT=1 pytest tests/test_frozen_core.py -q"
    )


def test_frozen_core_public_signatures() -> None:
    """Constitution Sec. 32: public signatures of models.py/interfaces.py must not drift
    without a deliberate, reviewed acceptance of the new baseline."""
    import os

    actual = compute_frozen_core_signature()

    if os.environ.get(_ACCEPT_ENV_VAR) == "1":
        FIXTURE_PATH.write_text(actual)
        return

    assert FIXTURE_PATH.exists(), (
        f"{FIXTURE_PATH} is missing. Regenerate it with: "
        f"KRI_ACCEPT_FROZEN_CORE_DRIFT=1 pytest tests/test_frozen_core.py -q"
    )
    expected = FIXTURE_PATH.read_text()

    if actual != expected:
        import difflib

        diff = "\n".join(
            difflib.unified_diff(
                expected.splitlines(),
                actual.splitlines(),
                fromfile="tests/fixtures/frozen_core_signature.txt (committed)",
                tofile="current kri.common.{models,interfaces} public API",
                lineterm="",
            )
        )
        raise AssertionError(
            "Frozen-core public API drift detected (Constitution Sec. 32).\n"
            "If this drift is intentional and has been reviewed, accept the new "
            "baseline with:\n"
            "  KRI_ACCEPT_FROZEN_CORE_DRIFT=1 pytest tests/test_frozen_core.py -q\n\n"
            f"{diff}"
        )
