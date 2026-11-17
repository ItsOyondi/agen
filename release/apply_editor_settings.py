"""Write the Agen icon theme and keyword colors into Cursor and VS Code settings.

The install package runs this once. It keeps every other setting already there.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path


def parse_jsonc(text: str) -> dict:
    out: list[str] = []
    i = 0
    in_string = False
    escaped = False
    while i < len(text):
        char = text[i]
        if in_string:
            out.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            i += 1
            continue
        if char == '"':
            in_string = True
            out.append(char)
            i += 1
            continue
        if char == "/" and i + 1 < len(text) and text[i + 1] == "/":
            i += 2
            while i < len(text) and text[i] not in "\r\n":
                i += 1
            continue
        if char == "/" and i + 1 < len(text) and text[i + 1] == "*":
            i += 2
            while i + 1 < len(text) and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i = min(i + 2, len(text))
            continue
        out.append(char)
        i += 1
    cleaned = re.sub(r",(\s*[}\]])", r"\1", "".join(out)).strip()
    if not cleaned:
        return {}
    value = json.loads(cleaned)
    if not isinstance(value, dict):
        raise SystemExit("editor settings must be a JSON object")
    return value


def settings_paths() -> list[Path]:
    appdata = Path(os.environ.get("APPDATA", ""))
    local = Path(os.environ.get("LOCALAPPDATA", ""))
    home = Path(os.environ.get("USERPROFILE", ""))
    found: list[Path] = []
    if (local / "Programs" / "cursor").is_dir() or (appdata / "Cursor").is_dir() or (home / ".cursor").is_dir():
        found.append(appdata / "Cursor" / "User" / "settings.json")
    if (local / "Programs" / "Microsoft VS Code").is_dir() or (appdata / "Code").is_dir() or (home / ".vscode").is_dir():
        found.append(appdata / "Code" / "User" / "settings.json")
    return found


def agen_rules() -> list[dict]:
    package = Path(__file__).resolve().parent / "editor" / "package.json"
    data = json.loads(package.read_text(encoding="utf-8"))
    colors = data.get("configurationDefaults", {}).get("editor.tokenColorCustomizations", {})
    rules = colors.get("textMateRules", [])
    if not rules:
        raise SystemExit("the editor package has no Agen colors")
    return rules


def merge(path: Path, rules: list[dict]) -> None:
    current = parse_jsonc(path.read_text(encoding="utf-8")) if path.is_file() else {}
    current["workbench.iconTheme"] = "agen-seti"
    colors = current.get("editor.tokenColorCustomizations")
    if not isinstance(colors, dict):
        colors = {}
    existing = colors.get("textMateRules")
    if not isinstance(existing, list):
        existing = []
    seen = {str(rule.get("scope")) for rule in existing if isinstance(rule, dict)}
    for rule in rules:
        if str(rule.get("scope")) not in seen:
            existing.append(rule)
            seen.add(str(rule.get("scope")))
    colors["textMateRules"] = existing
    current["editor.tokenColorCustomizations"] = colors
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(current, indent=4) + "\n", encoding="utf-8")
    print(f"saved editor settings: {path}")


def main() -> None:
    rules = agen_rules()
    paths = settings_paths()
    if not paths:
        print("no Cursor or VS Code settings folder found; the extension will apply colors when it starts")
        return
    for path in paths:
        merge(path, rules)


if __name__ == "__main__":
    main()
