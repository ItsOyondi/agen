"""Load a ``.env`` file into the process environment.

Values already set in the environment win, so a shell export overrides the file.
The file is never required.
"""

from __future__ import annotations

import os
from pathlib import Path


def load_dotenv(path: Path) -> bool:
    path = Path(path)
    if not path.is_file():
        return False
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)
    return True
