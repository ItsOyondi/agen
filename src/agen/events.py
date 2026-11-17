"""Built-in monitoring: structured event log, console output and alerting."""

from __future__ import annotations

import json
import os
import sys
import threading
from collections import Counter
from pathlib import Path
from typing import Any, TextIO

from .durable import utc_now
from .errors import AgenError

_TEMPLATES = {
    "workflow.started": "workflow {workflow} started (run {run_id})",
    "workflow.resumed": "workflow {workflow} resumed from checkpoint: {completed} step(s) already done (run {run_id})",
    "workflow.cached": "workflow {workflow} already completed; returning saved result (run {run_id})",
    "workflow.completed": "workflow {workflow} completed",
    "workflow.failed": "workflow {workflow} failed: {error}",
    "workflow.interrupted": "workflow {workflow} interrupted; progress saved (run {run_id})",
    "step.completed": "  step {step} ok (attempt {attempt}, {duration:.2f}s)",
    "step.replayed": "  step {step} replayed from journal",
    "step.retrying": "  step {step} failed on attempt {attempt}: {error} -> retrying in {delay:.2f}s",
    "step.fallback": "  step {step} exhausted retries ({error}) -> running fallback",
    "step.recovered": "  step {step} recovered via fallback",
    "step.failed": "  step {step} failed permanently after {attempts} attempt(s): {error}",
    "service.started": "service {service} started (restart {restart})",
    "service.restarting": "service {service} crashed: {error} -> restart {restart}/{max} in {delay:.2f}s",
    "service.stopped": "service {service} finished",
    "deploy.applied": "environment {env} is up to date",
    "resource.created": "  created {kind} {name}",
    "resource.updated": "  updated {kind} {name}",
    "resource.deleted": "  deleted {kind} {name}",
    "resource.retrying": "  {action} {name} failed on attempt {attempt}: {error} -> retrying in {delay:.2f}s",
    "parallel.completed": "{kind}: {tasks} task(s) on {workers} worker(s) in {duration:.2f}s",
    "secret.loaded": "loaded {count} secret(s) from vault {vault}",
    "runtime.fresh": "discarded {removed} saved workflow run(s)",
    "runtime.crash_simulated": "simulating a crash after step {step}; run again to resume",
    "custom": "event {name}: {data}",
    "alert": "ALERT: {message}",
}

_LOG_ONLY = frozenset({"step.started", "deploy.planned", "cache.hit"})


class EventLog:
    def __init__(self, path: Path | None, *, quiet: bool = False, stream: TextIO | None = None) -> None:
        self.path = Path(path) if path else None
        self.quiet = quiet
        self.stream = stream or sys.stderr
        self.counts: Counter[str] = Counter()
        self._lock = threading.RLock()
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def emit(self, event: str, **fields: Any) -> None:
        with self._lock:
            self.counts[event] += 1
            if self.path:
                record = {"ts": utc_now(), "event": event, **fields}
                with open(self.path, "a", encoding="utf-8") as fh:
                    fh.write(json.dumps(record, default=str) + "\n")
            if event in _LOG_ONLY or (self.quiet and event != "alert"):
                return
            self._write(self._format(event, fields))

    def alert(self, message: str, **fields: Any) -> None:
        """Alerts always reach the console and, if configured, a webhook (Slack/Teams/etc.)."""
        self.emit("alert", message=message, **fields)
        webhook = os.environ.get("AGEN_ALERT_WEBHOOK")
        if not webhook:
            return
        from . import net

        try:
            net.request("POST", webhook, body={"text": f"[agen] {message}", **fields}, timeout=5)
        except AgenError as exc:
            self._write(f"warning: could not deliver alert to webhook: {exc}")

    def info(self, text: str) -> None:
        if not self.quiet:
            self._write(text)

    def summary(self) -> str:
        c = self.counts
        return (
            f"summary: {c['step.completed'] + c['step.recovered']} step(s) ran, "
            f"{c['step.replayed']} replayed, {c['step.retrying']} retried, "
            f"{c['step.recovered']} recovered by fallback, {c['step.failed']} failed, "
            f"{c['service.restarting']} service restart(s), {c['alert']} alert(s), "
            f"{c['parallel.completed']} parallel batch(es), {c['cache.hit']} cache hit(s)"
        )

    def _write(self, text: str) -> None:
        with self._lock:
            for line in text.splitlines() or [""]:
                self.stream.write(f"[agen] {line}\n")
            self.stream.flush()

    @staticmethod
    def _format(event: str, fields: dict[str, Any]) -> str:
        template = _TEMPLATES.get(event)
        if template:
            readable = {k: json.dumps(v, default=str) if isinstance(v, (dict, list)) else v
                        for k, v in fields.items()}
            try:
                return template.format(**readable)
            except (KeyError, ValueError, IndexError):
                pass
        return f"{event} {json.dumps(fields, default=str)}"
