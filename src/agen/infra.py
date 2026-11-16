"""Infrastructure as code: declared resources are reconciled against saved state.

``deploy`` computes a plan (create / update / delete) by diffing declared
resources with the environment's state file and applies it through a provider.
Every change is retried on transient failures and checkpointed when it
finishes, so an interrupted deploy picks up where it stopped on the next run.
"""

from __future__ import annotations

import random
import re
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Protocol

from .durable import atomic_write_json, encode, read_json
from .errors import AgenRuntimeError
from .events import EventLog
from .resilience import RetryPolicy, call_with_retries
from .values import Resource

_SAFE_KEY = re.compile(r"^[A-Za-z0-9_-][A-Za-z0-9._-]{0,127}$")
_PAST = {"create": "created", "update": "updated", "delete": "deleted"}
_SYMBOL = {"create": "+", "update": "~", "delete": "-"}


@dataclass(frozen=True)
class Change:
    action: str  # create | update | delete | noop
    name: str
    kind: str
    config: dict[str, Any] | None


class Provider(Protocol):
    def apply(self, env: str, name: str, kind: str, config: dict[str, Any]) -> None: ...
    def delete(self, env: str, name: str, kind: str) -> None: ...


class LocalProvider:
    """A simulated cloud: each resource is a directory under ``.agen/cloud/<env>/<name>``."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def apply(self, env: str, name: str, kind: str, config: dict[str, Any]) -> None:
        atomic_write_json(self.root / env / name / "resource.json", {"kind": kind, "config": config})

    def delete(self, env: str, name: str, kind: str) -> None:
        shutil.rmtree(self.root / env / name, ignore_errors=True)


PROVIDERS: dict[str, Callable[[Path], Provider]] = {
    "local": lambda state_dir: LocalProvider(state_dir / "cloud"),
}


def format_plan(env: str, changes: list[Change]) -> str:
    pending = [c for c in changes if c.action != "noop"]
    counts = {a: sum(c.action == a for c in changes) for a in ("create", "update", "delete")}
    header = (f"plan for '{env}': {counts['create']} to create, {counts['update']} to update, "
              f"{counts['delete']} to delete")
    if not pending:
        return f"plan for '{env}': no changes"
    return "\n".join([header] + [f"  {_SYMBOL[c.action]} {c.kind} {c.name}" for c in pending])


class InfraManager:
    def __init__(self, state_dir: Path, events: EventLog | None = None) -> None:
        self.state_dir = Path(state_dir)
        self.events = events or EventLog(None, quiet=True)
        self.declared: dict[str, Resource] = {}

    def declare(self, resource: Resource) -> None:
        if resource.name in self.declared:
            raise AgenRuntimeError(f"resource '{resource.name}' is already declared", retryable=False)
        self.declared[resource.name] = resource

    # State -------------------------------------------------------------------

    def _state_path(self, env: str) -> Path:
        return self.state_dir / "infra" / f"{env}.json"

    def load_state(self, env: str) -> dict[str, Any]:
        path = self._state_path(env)
        return read_json(path) if path.exists() else {"provider": None, "resources": {}}

    def environments(self) -> dict[str, dict[str, Any]]:
        directory = self.state_dir / "infra"
        if not directory.exists():
            return {}
        return {p.stem: read_json(p) for p in sorted(directory.glob("*.json"))}

    # Plan / apply ------------------------------------------------------------

    def plan(self, env: str) -> list[Change]:
        """Resources are applied in declaration order, which is always dependency order:
        a resource can only reference resources declared before it."""
        current = self.load_state(env)["resources"]
        changes: list[Change] = []
        for res in self.declared.values():
            desired = encode(res.config)
            existing = current.get(res.name)
            if existing is None:
                action = "create"
            elif existing["kind"] != res.kind or existing["config"] != desired:
                action = "update"
            else:
                action = "noop"
            changes.append(Change(action, res.name, res.kind, desired))
        for name in reversed(list(current)):
            if name not in self.declared:
                changes.append(Change("delete", name, current[name]["kind"], None))
        return changes

    def apply(
        self,
        env: str,
        changes: list[Change],
        provider_name: str,
        policy: RetryPolicy,
        *,
        rng: random.Random,
        sleep: Callable[[float], None],
    ) -> None:
        factory = PROVIDERS.get(provider_name)
        if factory is None:
            raise AgenRuntimeError(
                f"unknown provider '{provider_name}' (available: {', '.join(sorted(PROVIDERS))})",
                retryable=False,
            )
        provider = factory(self.state_dir)
        state = self.load_state(env)
        state["provider"] = provider_name
        resources = state["resources"]

        for change in changes:
            if change.action == "noop":
                continue

            def perform(change: Change = change) -> None:
                try:
                    if change.action == "delete":
                        provider.delete(env, change.name, change.kind)
                    else:
                        provider.apply(env, change.name, change.kind, change.config or {})
                except OSError as exc:
                    raise AgenRuntimeError(f"{change.action} {change.kind} '{change.name}' failed: {exc}") from exc

            def on_retry(attempt: int, exc: Exception, delay: float, change: Change = change) -> None:
                self.events.emit("resource.retrying", action=change.action, name=change.name,
                                 attempt=attempt, error=str(exc), delay=delay)

            call_with_retries(perform, policy, rng=rng, sleep=sleep, on_retry=on_retry)
            if change.action == "delete":
                resources.pop(change.name, None)
            else:
                resources[change.name] = {"kind": change.kind, "config": change.config}
            atomic_write_json(self._state_path(env), state)
            self.events.emit(f"resource.{_PAST[change.action]}", env=env, name=change.name, kind=change.kind)

    # Data plane for the local provider ---------------------------------------

    def _object_path(self, env: str, resource: Resource, key: Any) -> Path:
        if resource.name not in self.load_state(env)["resources"]:
            raise AgenRuntimeError(
                f"resource '{resource.name}' is not deployed in environment '{env}'; "
                "use it inside a deploy block",
                retryable=False,
            )
        if not isinstance(key, str) or not _SAFE_KEY.match(key):
            raise AgenRuntimeError(f"invalid object key {key!r}: use letters, digits, '.', '_' or '-'",
                                   retryable=False)
        return self.state_dir / "cloud" / env / resource.name / "data" / f"{key}.json"

    def put(self, env: str, resource: Resource, key: Any, value: Any) -> None:
        atomic_write_json(self._object_path(env, resource, key), encode(value))

    def get(self, env: str, resource: Resource, key: Any, default: Any = None) -> Any:
        path = self._object_path(env, resource, key)
        return read_json(path) if path.exists() else default
