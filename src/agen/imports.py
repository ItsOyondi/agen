"""Resolve ``@import`` and ``@at ... @import`` library paths.

A library name is a dotted or slashed path of identifiers, such as ``mathx`` or
``lib.mathx``. The file is ``<name>.agn``, looked up next to the importing file,
then in a ``lib`` folder there, then in each directory of ``AGEN_LIB``.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .errors import AgenRuntimeError

_PART = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_loader: object | None = None


def install(loader: object) -> None:
    global _loader
    _loader = loader


def active_loader() -> object:
    if _loader is None:
        raise AgenRuntimeError("no library loader is active", retryable=False)
    return _loader


def library_roots(base: Path) -> list[Path]:
    roots = [base, base / "lib"]
    for entry in os.environ.get("AGEN_LIB", "").split(os.pathsep):
        if entry.strip():
            roots.append(Path(entry.strip()))
    return roots


def resolve_library(spec: str, base: Path) -> Path:
    parts = _parts(spec)
    relative = Path(*parts).with_suffix(".agn")
    for root in library_roots(base):
        candidate = (root / relative).resolve()
        if candidate.is_file() and _is_inside(candidate, root.resolve()):
            return candidate
    searched = ", ".join(str(root / relative) for root in library_roots(base))
    raise AgenRuntimeError(f"library '{spec}' was not found. Looked for {searched}", retryable=False)


def _parts(spec: str) -> list[str]:
    text = spec.replace("\\", "/").strip("/")
    parts = [part for part in text.replace(".", "/").split("/") if part]
    if not parts or any(not _PART.fullmatch(part) for part in parts):
        raise AgenRuntimeError(
            f"library name '{spec}' must be identifiers separated by '.' or '/'",
            retryable=False,
        )
    return parts


def _is_inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True
