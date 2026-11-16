"""Durable execution state: the workflow journal.

Each workflow run gets a JSON journal. Each completed step writes the variables
it changed to the journal before the workflow moves on. When an interrupted or
failed run is started again, completed steps are replayed from the journal
instead of running again, so the workflow continues where it stopped.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .errors import AgenRuntimeError, AgenTypeError
from .values import Agent, BoundMethod, Builtin, Class, Function, Instance, Resource, Workflow, type_name

SCHEMA_VERSION = 1

Resolver = Callable[[str, str], Any]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def encode(value: Any) -> Any:
    """Convert a runtime value into JSON-safe data; named objects become references."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, list):
        return [encode(v) for v in value]
    if isinstance(value, dict):
        return {str(k): encode(v) for k, v in value.items()}
    if isinstance(value, Resource):
        return {"$ref": "resource", "name": value.name}
    if isinstance(value, Agent):
        return {"$ref": "agent", "name": value.name}
    if isinstance(value, Workflow):
        return {"$ref": "workflow", "name": value.name}
    if isinstance(value, (Function, Builtin)):
        return {"$ref": "fn", "name": value.name}
    if isinstance(value, Class):
        return {"$ref": "class", "name": value.name}
    if isinstance(value, Instance):
        return {"$ref": "instance", "class": value.klass.name, "fields": encode(value.fields)}
    if isinstance(value, BoundMethod):
        return {"$ref": "method", "class": value.owner.name, "name": value.method.name,
                "instance": encode(value.instance)}
    raise AgenTypeError(f"cannot persist a value of type {type_name(value)}")


def decode(value: Any, resolve: Resolver) -> Any:
    if isinstance(value, list):
        return [decode(v, resolve) for v in value]
    if isinstance(value, dict):
        ref = value.get("$ref")
        if ref == "instance":
            klass = resolve("class", value["class"])
            if not isinstance(klass, Class):
                raise AgenRuntimeError(f"journal references unknown class '{value['class']}'", retryable=False)
            instance = Instance(klass)
            instance.fields = decode(value.get("fields", {}), resolve)
            return instance
        if ref == "method":
            klass = resolve("class", value["class"])
            if not isinstance(klass, Class):
                raise AgenRuntimeError(f"journal references unknown class '{value['class']}'", retryable=False)
            found = klass.lookup_method(value["name"])
            if found is None:
                raise AgenRuntimeError(
                    f"journal references unknown method '{value['name']}' of class '{klass.name}'",
                    retryable=False,
                )
            method, owner = found
            return BoundMethod(decode(value["instance"], resolve), method, owner)
        if ref is not None and set(value) == {"$ref", "name"}:
            return resolve(ref, value["name"])
        return {k: decode(v, resolve) for k, v in value.items()}
    return value


def run_id_for(workflow: str, args: list[Any], definition: str = "") -> str:
    """Run id from the workflow name, its arguments, and a hash of the program.

    The same call resumes the same run. Editing the program changes ``definition``,
    so the next run starts fresh instead of returning a saved answer.
    """
    payload = json.dumps([workflow, definition, encode(args)], sort_keys=True, separators=(",", ":"))
    return f"{workflow}-{hashlib.sha256(payload.encode()).hexdigest()[:12]}"


def atomic_write_json(path: Path, data: Any) -> None:
    """Write via temp file + rename so a crash never leaves a half-written journal."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(data, fh, indent=2)
        fh.flush()
        os.fsync(fh.fileno())
    # Sync clients (OneDrive, Dropbox) and antivirus can briefly lock the target on Windows.
    for attempt in range(5):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 4:
                raise
            time.sleep(0.05 * (attempt + 1))


def read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise AgenRuntimeError(f"state file {path} is corrupt: {exc}", retryable=False) from exc


class RunRecord:
    def __init__(self, path: Path, data: dict[str, Any]) -> None:
        self._path = path
        self.data = data
        # Parallel branches of one workflow share this record.
        self._lock = threading.RLock()

    @property
    def run_id(self) -> str:
        return self.data["run_id"]

    @property
    def workflow(self) -> str:
        return self.data["workflow"]

    @property
    def status(self) -> str:
        return self.data["status"]

    @property
    def steps(self) -> dict[str, dict[str, Any]]:
        return self.data["steps"]

    @property
    def result(self) -> Any:
        return self.data.get("result")

    def save(self) -> None:
        with self._lock:
            self.data["updated_at"] = utc_now()
            atomic_write_json(self._path, self.data)

    def record_step(self, key: str, entry: dict[str, Any]) -> None:
        with self._lock:
            self.steps[key] = entry
            self.save()

    def set_status(self, status: str, **fields: Any) -> None:
        with self._lock:
            self.data["status"] = status
            self.data.update(fields)
            self.save()


class JournalStore:
    def __init__(self, directory: Path) -> None:
        self.directory = Path(directory)

    def open(self, run_id: str, workflow: str, args: list[Any]) -> RunRecord:
        path = self.directory / f"{run_id}.json"
        if path.exists():
            data = read_json(path)
            if data.get("version") != SCHEMA_VERSION:
                raise AgenRuntimeError(f"journal {path} has an unsupported version", retryable=False)
            return RunRecord(path, data)
        return RunRecord(path, {
            "version": SCHEMA_VERSION,
            "run_id": run_id,
            "workflow": workflow,
            "args": encode(args),
            "status": "pending",
            "attempts": 0,
            "created_at": utc_now(),
            "steps": {},
            "result": None,
        })

    def runs(self) -> list[dict[str, Any]]:
        if not self.directory.exists():
            return []
        return [read_json(p) for p in sorted(self.directory.glob("*.json"))]

    def clear(self) -> int:
        if not self.directory.exists():
            return 0
        removed = 0
        for path in self.directory.glob("*.json"):
            path.unlink()
            removed += 1
        return removed
