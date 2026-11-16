from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Callable

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from agen import Interpreter, RuntimeOptions, parse  # noqa: E402
from agen.values import Builtin  # noqa: E402


def make_interp(state_dir: str | Path, **options: Any) -> Interpreter:
    return Interpreter(RuntimeOptions(state_dir=Path(state_dir), quiet=True, seed=1, **options))


def run_source(source: str, state_dir: str | Path, *, builtins: dict[str, Callable] | None = None,
               **options: Any) -> Interpreter:
    interp = make_interp(state_dir, **options)
    for name, fn in (builtins or {}).items():
        interp.globals.set(name, Builtin(name, fn))
    interp.run(parse(source))
    return interp
