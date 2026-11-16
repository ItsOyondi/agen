"""Tree-walking interpreter with a durable, self-healing runtime.

Fault tolerance is part of the language semantics, not library code:

* ``step``      runs with automatic retries, backoff, timeouts and an optional
                fallback. Its variable writes are journaled when it completes.
* ``workflow``  is durable. Re-running an interrupted or failed workflow replays
                completed steps from the journal and continues from the first
                unfinished step.
* ``service``   is supervised. Crashes restart it with backoff, and an alert is
                raised if it stays down.
* ``deploy``    reconciles declared infrastructure (plan, then apply) before
                running application logic in that environment.
* ``parallel``  runs independent statements concurrently. ``pmap``, ``ask_all``
                and ``jev_all`` use the same engine for lists.
"""

from __future__ import annotations

import copy
import hashlib
import json
import os
import random
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, wait
from contextlib import contextmanager
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Iterator, Literal

from . import nodes as n
from .builtins import make_builtins
from .durable import JournalStore, RunRecord, decode, encode, run_id_for, utc_now
from .errors import (
    AgenError,
    AgenRuntimeError,
    AgenTimeoutError,
    AgenTypeError,
    ServiceFailedError,
    StepFailedError,
)
from .events import EventLog
from .infra import InfraManager, format_plan
from .resilience import RetryPolicy
from .values import (
    Agent,
    BoundMethod,
    Builtin,
    Class,
    Environment,
    Function,
    Instance,
    Resource,
    SuperView,
    Workflow,
    binary_op,
    format_value,
    get_index,
    get_member,
    is_int,
    is_number,
    set_index,
    set_member,
    truthy,
    type_name,
)

CRASH_EXIT_CODE = 75
DEFAULT_WORKERS = 8
_DONE = ("completed", "fallback")
_NO_RETURN = object()
_DECLARATIONS = (n.Let, n.FnDef, n.ClassDef, n.WorkflowDef, n.ResourceDef, n.AgentDef)

Mode = Literal["run", "plan", "declare"]


def _fingerprint(value: Any) -> Any:
    """Structure of an AST node, ignoring line numbers."""
    if isinstance(value, (list, tuple)):
        return [_fingerprint(item) for item in value]
    if isinstance(value, n.Node):
        return [type(value).__name__, {
            item.name: _fingerprint(getattr(value, item.name))
            for item in fields(value) if item.name != "line"
        }]
    return value


def _definition_id(program: list[n.Stmt]) -> str:
    payload = json.dumps(_fingerprint(program), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


class ReturnSignal(Exception):
    def __init__(self, value: Any) -> None:
        super().__init__()
        self.value = value


@dataclass
class RuntimeOptions:
    state_dir: Path
    mode: Mode = "run"
    crash_after: str | None = None
    seed: int | None = None
    quiet: bool = False


@dataclass
class _RunContext:
    run: RunRecord
    counters: dict[str, int] = field(default_factory=dict)
    in_step: bool = False
    # Step keys inside parallel branches are prefixed (parallel0.1.fetch) so replay
    # stays deterministic no matter which branch finishes first.
    prefix: str = ""


class _ThreadState(threading.local):
    """Execution stacks are per thread so parallel branches don't share them."""

    def __init__(self) -> None:
        self.deadlines: list[float] = []
        self.runs: list[_RunContext] = []
        self.methods: list[tuple[Instance, Class]] = []


class Interpreter:
    def __init__(self, options: RuntimeOptions, *, events: EventLog | None = None) -> None:
        self.options = options
        self.state_dir = Path(options.state_dir)
        self.events = events or EventLog(self.state_dir / "events.jsonl", quiet=options.quiet)
        self.rng = random.Random(options.seed)
        self.journal = JournalStore(self.state_dir / "runs")
        self.infra = InfraManager(self.state_dir, self.events)
        self.current_env = "default"
        self.globals = Environment()
        for name, builtin in make_builtins(self).items():
            self.globals.set(name, builtin)

        self._ts = _ThreadState()
        self._cache: dict[str, Any] = {}
        self._cache_lock = threading.Lock()
        self._exec: dict[type, Callable[[Any, Environment], None]] = {
            n.Let: self._exec_let,
            n.Assign: self._exec_assign,
            n.ExprStmt: self._exec_expr,
            n.If: self._exec_if,
            n.While: self._exec_while,
            n.For: self._exec_for,
            n.Return: self._exec_return,
            n.FnDef: self._exec_fn,
            n.ClassDef: self._exec_class,
            n.WorkflowDef: self._exec_workflow,
            n.Step: self._exec_step,
            n.ResourceDef: self._exec_resource,
            n.AgentDef: self._exec_agent,
            n.Service: self._exec_service,
            n.Deploy: self._exec_deploy,
            n.Parallel: self._exec_parallel,
        }
        self._eval: dict[type, Callable[[Any, Environment], Any]] = {
            n.Literal: lambda node, env: node.value,
            n.Name: lambda node, env: env.lookup(node.id),
            n.ListLit: lambda node, env: [self.evaluate(i, env) for i in node.items],
            n.MapLit: lambda node, env: {k: self.evaluate(v, env) for k, v in node.entries},
            n.Unary: self._eval_unary,
            n.Binary: lambda node, env: binary_op(node.op, self.evaluate(node.left, env),
                                                  self.evaluate(node.right, env)),
            n.Logical: self._eval_logical,
            n.Call: lambda node, env: self.call(self.evaluate(node.callee, env),
                                                [self.evaluate(a, env) for a in node.args]),
            n.Index: lambda node, env: get_index(self.evaluate(node.target, env),
                                                 self.evaluate(node.index, env)),
            n.Member: lambda node, env: get_member(self.evaluate(node.target, env), node.name),
            n.Super: self._eval_super,
        }
        self._definition = ""

    @property
    def _deadlines(self) -> list[float]:
        return self._ts.deadlines

    @property
    def _runs(self) -> list[_RunContext]:
        return self._ts.runs

    @property
    def _methods(self) -> list[tuple[Instance, Class]]:
        return self._ts.methods

    # Entry points ------------------------------------------------------------

    def run(self, program: list[n.Stmt]) -> None:
        """Execute a program. ``plan`` mode only evaluates declarations and prints deploy
        plans; ``declare`` mode only evaluates declarations."""
        mode = self.options.mode
        self._definition = _definition_id(program)
        for stmt in program:
            if mode == "declare" and not isinstance(stmt, _DECLARATIONS):
                continue
            if mode == "plan" and not isinstance(stmt, (*_DECLARATIONS, n.Deploy)):
                continue
            try:
                self.execute(stmt, self.globals)
            except ReturnSignal:
                raise AgenRuntimeError("'return' outside of a function", stmt.line, retryable=False) from None

    def execute(self, node: n.Stmt, env: Environment) -> None:
        self._check_deadline()
        try:
            self._exec[type(node)](node, env)
        except AgenError as exc:
            if exc.line is None:
                exc.line = node.line
            raise

    def exec_block(self, body: n.Block, env: Environment) -> None:
        for stmt in body:
            self.execute(stmt, env)

    def evaluate(self, node: n.Expr, env: Environment) -> Any:
        return self._eval[type(node)](node, env)

    def call(self, callee: Any, args: list[Any]) -> Any:
        if isinstance(callee, Workflow):
            return self._call_workflow(callee, args)
        if isinstance(callee, Class):
            return self._construct(callee, args)
        if isinstance(callee, SuperView):
            return self._call_super(callee, args)
        if isinstance(callee, BoundMethod):
            return self._call_method(callee.instance, callee.method, callee.owner, args)
        if isinstance(callee, Function):
            return self._call_function(callee, args)
        if isinstance(callee, Builtin):
            try:
                return callee.fn(*args)
            except (AgenError, ReturnSignal):
                raise
            except (TypeError, ValueError, KeyError, IndexError) as exc:
                raise AgenTypeError(f"{callee.name}(): {exc}") from exc
            except Exception as exc:
                raise AgenRuntimeError(f"{callee.name}(): {exc}") from exc
        raise AgenTypeError(f"{type_name(callee)} is not callable")

    # Time budget -------------------------------------------------------------

    @contextmanager
    def _deadline(self, seconds: float | None) -> Iterator[None]:
        if seconds is None:
            yield
            return
        limit = time.monotonic() + seconds
        if self._deadlines:
            limit = min(limit, self._deadlines[-1])
        self._deadlines.append(limit)
        try:
            yield
        finally:
            self._deadlines.pop()

    def _check_deadline(self) -> None:
        if self._deadlines and time.monotonic() > self._deadlines[-1]:
            raise AgenTimeoutError("timed out")

    def remaining_time(self) -> float | None:
        if not self._deadlines:
            return None
        return max(0.001, self._deadlines[-1] - time.monotonic())

    def sleep(self, seconds: Any) -> None:
        """Cooperative sleep: wakes early to enforce the active timeout."""
        if not is_number(seconds) or seconds < 0:
            raise AgenTypeError("sleep() expects a non-negative number of seconds")
        end = time.monotonic() + seconds
        while True:
            self._check_deadline()
            remaining = end - time.monotonic()
            if remaining <= 0:
                return
            time.sleep(min(remaining, 0.05))

    # Plain statements --------------------------------------------------------

    def _exec_let(self, node: n.Let, env: Environment) -> None:
        env.set(node.name, self.evaluate(node.value, env))

    def _exec_assign(self, node: n.Assign, env: Environment) -> None:
        value = self.evaluate(node.value, env)
        target = node.target
        if isinstance(target, n.Name):
            if target.id == "self":
                raise AgenTypeError("cannot assign to 'self'")
            env.set(target.id, value)
        elif isinstance(target, n.Index):
            set_index(self.evaluate(target.target, env), self.evaluate(target.index, env), value)
        else:
            set_member(self.evaluate(target.target, env), target.name, value)

    def _exec_expr(self, node: n.ExprStmt, env: Environment) -> None:
        self.evaluate(node.expr, env)

    def _exec_if(self, node: n.If, env: Environment) -> None:
        if truthy(self.evaluate(node.condition, env)):
            self.exec_block(node.then_body, env)
        elif node.else_body is not None:
            self.exec_block(node.else_body, env)

    def _exec_while(self, node: n.While, env: Environment) -> None:
        while truthy(self.evaluate(node.condition, env)):
            self._check_deadline()
            self.exec_block(node.body, env)

    def _exec_for(self, node: n.For, env: Environment) -> None:
        iterable = self.evaluate(node.iterable, env)
        if not isinstance(iterable, (list, dict, str)):
            raise AgenTypeError(f"cannot iterate over {type_name(iterable)}")
        for item in list(iterable):
            env.set(node.var, item)
            self.exec_block(node.body, env)

    def _exec_return(self, node: n.Return, env: Environment) -> None:
        raise ReturnSignal(None if node.value is None else self.evaluate(node.value, env))

    def _exec_fn(self, node: n.FnDef, env: Environment) -> None:
        env.set(node.name, Function(node.name, node.params, node.body, env))

    def _exec_class(self, node: n.ClassDef, env: Environment) -> None:
        parent = None
        if node.parent is not None:
            parent = env.lookup(node.parent)
            if not isinstance(parent, Class):
                raise AgenTypeError(f"'{node.parent}' is not a class")
        methods = {method.name: Function(method.name, method.params, method.body, env) for method in node.methods}
        env.set(node.name, Class(node.name, parent, methods))

    def _eval_super(self, node: n.Super, env: Environment) -> SuperView:
        if not self._methods:
            raise AgenRuntimeError("'super' can only be used inside a method", retryable=False)
        instance, defining = self._methods[-1]
        if defining.parent is None:
            raise AgenRuntimeError(f"class '{defining.name}' has no parent", retryable=False)
        return SuperView(instance, defining.parent)

    def _exec_workflow(self, node: n.WorkflowDef, env: Environment) -> None:
        env.set(node.name, Workflow(node.name, node.params, node.body, env))

    def _exec_resource(self, node: n.ResourceDef, env: Environment) -> None:
        resource = Resource(node.kind, node.name, self._eval_config(node.config, env))
        self.infra.declare(resource)
        env.set(node.name, resource)

    def _exec_agent(self, node: n.AgentDef, env: Environment) -> None:
        env.set(node.name, Agent(node.name, self._eval_config(node.config, env)))

    # Expressions -------------------------------------------------------------

    def _eval_unary(self, node: n.Unary, env: Environment) -> Any:
        value = self.evaluate(node.operand, env)
        if node.op == "not":
            return not truthy(value)
        if not is_number(value):
            raise AgenTypeError(f"cannot negate {type_name(value)}")
        return -value

    def _eval_logical(self, node: n.Logical, env: Environment) -> Any:
        left = self.evaluate(node.left, env)
        if node.op == "and":
            return self.evaluate(node.right, env) if truthy(left) else left
        return left if truthy(left) else self.evaluate(node.right, env)

    # Calls -------------------------------------------------------------------

    @staticmethod
    def _check_arity(fn: Function, args: list[Any]) -> None:
        if len(args) != len(fn.params):
            raise AgenTypeError(f"{fn.name}() expects {len(fn.params)} argument(s), got {len(args)}")

    def _call_method(self, instance: Instance, method: Function, owner: Class, args: list[Any]) -> Any:
        self._check_arity(method, args)
        frame = Environment(method.closure)
        frame.set("self", instance)
        for param, arg in zip(method.params, args):
            frame.set(param, arg)
        self._methods.append((instance, owner))
        try:
            self.exec_block(method.body, frame)
        except ReturnSignal as signal:
            return signal.value
        finally:
            self._methods.pop()
        return None

    def _construct(self, klass: Class, args: list[Any]) -> Instance:
        instance = Instance(klass)
        found = klass.lookup_method("init")
        if found is None:
            if args:
                raise AgenTypeError(f"{klass.name}() expects 0 argument(s), got {len(args)}")
            return instance
        method, owner = found
        self._call_method(instance, method, owner, args)
        return instance

    def _call_super(self, view: SuperView, args: list[Any]) -> None:
        found = view.klass.lookup_method("init")
        if found is None:
            if args:
                raise AgenTypeError(f"{view.klass.name}() expects 0 argument(s), got {len(args)}")
            return None
        method, owner = found
        self._call_method(view.instance, method, owner, args)
        return None

    def _call_function(self, fn: Function, args: list[Any]) -> Any:
        self._check_arity(fn, args)
        frame = Environment(fn.closure)
        for param, arg in zip(fn.params, args):
            frame.set(param, arg)
        try:
            self.exec_block(fn.body, frame)
        except ReturnSignal as signal:
            return signal.value
        return None

    def _call_workflow(self, wf: Workflow, args: list[Any]) -> Any:
        self._check_arity(wf, args)
        run_id = run_id_for(wf.name, args, self._definition)
        run = self.journal.open(run_id, wf.name, args)
        info = {"workflow": wf.name, "run_id": run_id}

        if run.status == "completed":
            self.events.emit("workflow.cached", **info)
            return decode(run.result, self._resolve)

        done = sum(1 for step in run.steps.values() if step["status"] in _DONE)
        self.events.emit("workflow.resumed" if done else "workflow.started", completed=done, **info)
        run.set_status("running", attempts=run.data.get("attempts", 0) + 1)

        frame = Environment(wf.closure)
        for param, arg in zip(wf.params, args):
            frame.set(param, arg)
        self._runs.append(_RunContext(run))
        try:
            try:
                self.exec_block(wf.body, frame)
                result = None
            except ReturnSignal as signal:
                result = signal.value
        except AgenError as exc:
            run.set_status("failed", error=str(exc))
            self.events.emit("workflow.failed", error=str(exc), **info)
            raise
        except KeyboardInterrupt:
            run.set_status("interrupted")
            self.events.emit("workflow.interrupted", **info)
            raise
        finally:
            self._runs.pop()

        run.set_status("completed", result=encode(result), error=None, completed_at=utc_now())
        self.events.emit("workflow.completed", **info)
        return result

    def _resolve(self, kind: str, name: str) -> Any:
        value = self.globals.vars.get(name)
        if value is None:
            raise AgenRuntimeError(f"journal references unknown {kind} '{name}'", retryable=False)
        return value

    # Durable steps -----------------------------------------------------------

    def _exec_step(self, node: n.Step, env: Environment) -> None:
        if not self._runs:
            raise AgenRuntimeError("'step' can only be used inside a workflow", retryable=False)
        ctx = self._runs[-1]
        if ctx.in_step:
            raise AgenRuntimeError("steps cannot be nested", retryable=False)

        # Steps inside loops get stable keys (fetch, fetch#1, fetch#2...) so replay is deterministic.
        occurrence = ctx.counters.get(node.name, 0)
        ctx.counters[node.name] = occurrence + 1
        key = ctx.prefix + (node.name if occurrence == 0 else f"{node.name}#{occurrence}")
        info = {"workflow": ctx.run.workflow, "run_id": ctx.run.run_id, "step": key}

        record = ctx.run.steps.get(key)
        if record and record["status"] in _DONE:
            for name, value in record["writes"].items():
                env.set(name, decode(value, self._resolve))
            self.events.emit("step.replayed", **info)
            if "returned" in record:
                raise ReturnSignal(decode(record["returned"], self._resolve))
            return

        policy = RetryPolicy.from_options(self._options(node.options, env, "step"))
        ctx.in_step = True
        try:
            self._run_step(ctx, key, node, env, policy, info)
        finally:
            ctx.in_step = False

    def _run_step(self, ctx: _RunContext, key: str, node: n.Step, env: Environment,
                  policy: RetryPolicy, info: dict[str, Any]) -> None:
        attempt = 0
        while True:
            attempt += 1
            snapshot = self._snapshot(env)
            started = time.monotonic()
            self.events.emit("step.started", attempt=attempt, **info)
            try:
                with self._deadline(policy.timeout):
                    returned = self._run_block(node.body, env)
            except AgenError as exc:
                self._restore(env, snapshot)
                if exc.retryable and attempt <= policy.retries:
                    delay = policy.delay(attempt, self.rng)
                    self.events.emit("step.retrying", attempt=attempt, error=str(exc), delay=delay, **info)
                    self.sleep(delay)
                    continue
                error = exc
                break
            self._commit(ctx, key, env, snapshot, "completed", attempt, returned, started, info)
            return

        if node.fallback is not None:
            self.events.emit("step.fallback", error=str(error), **info)
            snapshot = self._snapshot(env)
            started = time.monotonic()
            try:
                returned = self._run_block(node.fallback, env)
            except AgenError as exc:
                self._restore(env, snapshot)
                error = exc
            else:
                self._commit(ctx, key, env, snapshot, "fallback", attempt, returned, started, info)
                return

        ctx.run.record_step(key, {"status": "failed", "attempts": attempt, "error": str(error),
                                  "failed_at": utc_now()})
        self.events.emit("step.failed", attempts=attempt, error=str(error), **info)
        self.events.alert(f"step '{key}' of workflow '{ctx.run.workflow}' failed after "
                          f"{attempt} attempt(s): {error}", **info)
        raise StepFailedError(f"step '{key}' failed after {attempt} attempt(s): {error}",
                              retryable=error.retryable) from error

    def _run_block(self, body: n.Block, env: Environment) -> Any:
        try:
            self.exec_block(body, env)
        except ReturnSignal as signal:
            return signal.value
        return _NO_RETURN

    def _commit(self, ctx: _RunContext, key: str, env: Environment, snapshot: dict[str, Any],
                status: str, attempts: int, returned: Any, started: float, info: dict[str, Any]) -> None:
        duration = time.monotonic() - started
        writes = {}
        for name, value in env.vars.items():
            encoded = encode(value)
            if name not in snapshot or encode(snapshot[name]) != encoded:
                writes[name] = encoded
        entry: dict[str, Any] = {"status": status, "attempts": attempts, "writes": writes,
                                 "duration": round(duration, 4), "completed_at": utc_now()}
        if returned is not _NO_RETURN:
            entry["returned"] = encode(returned)
        ctx.run.record_step(key, entry)
        event = "step.completed" if status == "completed" else "step.recovered"
        self.events.emit(event, attempt=attempts, duration=duration, **info)

        if self.options.crash_after == key:
            self.events.emit("runtime.crash_simulated", **info)
            sys.stdout.flush()
            sys.stderr.flush()
            os._exit(CRASH_EXIT_CODE)

        if returned is not _NO_RETURN:
            raise ReturnSignal(returned)

    @staticmethod
    def _snapshot(env: Environment) -> dict[str, Any]:
        memo: dict[int, Any] = {}
        return {name: copy.deepcopy(value, memo) for name, value in env.vars.items()}

    @staticmethod
    def _restore(env: Environment, snapshot: dict[str, Any]) -> None:
        env.vars.clear()
        env.vars.update(snapshot)

    # Supervised services -----------------------------------------------------

    def _exec_service(self, node: n.Service, env: Environment) -> None:
        policy = RetryPolicy.from_options(self._options(node.options, env, "service"),
                                          retries_key="restarts",
                                          defaults=RetryPolicy(retries=3, backoff=0.5))
        info = {"service": node.name}
        restarts = 0
        while True:
            self.events.emit("service.started", restart=restarts, **info)
            try:
                with self._deadline(policy.timeout):
                    self.exec_block(node.body, env)
            except AgenError as exc:
                if exc.retryable and restarts < policy.retries:
                    restarts += 1
                    delay = policy.delay(restarts, self.rng)
                    self.events.emit("service.restarting", error=str(exc), restart=restarts,
                                     max=policy.retries, delay=delay, **info)
                    self.sleep(delay)
                    continue
                self.events.alert(f"service '{node.name}' is down after {restarts} restart(s): {exc}", **info)
                raise ServiceFailedError(f"service '{node.name}' failed: {exc}") from exc
            self.events.emit("service.stopped", restarts=restarts, **info)
            return

    # Deployments -------------------------------------------------------------

    def _exec_deploy(self, node: n.Deploy, env: Environment) -> None:
        options = self._options(node.options, env, "deploy")
        provider = str(options.pop("provider", "local"))
        policy = RetryPolicy.from_options(options, defaults=RetryPolicy(retries=3, backoff=0.5))

        changes = self.infra.plan(node.name)
        self.events.emit("deploy.planned", env=node.name, changes=[c.action for c in changes])
        self.events.info(format_plan(node.name, changes))
        if self.options.mode != "run":
            return

        self.infra.apply(node.name, changes, provider, policy, rng=self.rng, sleep=self.sleep)
        self.events.emit("deploy.applied", env=node.name, provider=provider)
        previous, self.current_env = self.current_env, node.name
        try:
            self.exec_block(node.body, env)
        finally:
            self.current_env = previous

    # Parallelism -------------------------------------------------------------

    def run_parallel(self, tasks: list[Callable[[], Any]], workers: Any, kind: str) -> list[Any]:
        """Run tasks on a thread pool and return their results in task order.

        Every task runs to completion, then the first failure (in task order) is
        raised. Threads overlap I/O such as model and HTTP calls; CPU-bound Agen
        code still runs one thread at a time.
        """
        if not tasks:
            return []
        pool_size = min(self._workers(workers), len(tasks))
        deadlines, runs, methods = list(self._deadlines), list(self._runs), list(self._methods)
        base = ""
        if runs:
            ctx = runs[-1]
            occurrence = ctx.counters.get(f"@{kind}", 0)
            ctx.counters[f"@{kind}"] = occurrence + 1
            base = f"{ctx.prefix}{kind}{occurrence}."

        def run_task(index: int, task: Callable[[], Any]) -> Any:
            state = self._ts
            state.deadlines, state.methods, state.runs = list(deadlines), list(methods), list(runs)
            if runs:
                parent = runs[-1]
                state.runs[-1] = _RunContext(parent.run, in_step=parent.in_step, prefix=f"{base}{index}.")
            try:
                return task()
            except ReturnSignal:
                raise AgenRuntimeError(f"'return' cannot leave a {kind} branch", retryable=False) from None

        started = time.monotonic()
        pool = ThreadPoolExecutor(max_workers=pool_size, thread_name_prefix=f"agen-{kind}")
        try:
            futures = [pool.submit(run_task, index, task) for index, task in enumerate(tasks)]
            pending = set(futures)
            while pending:
                # A timed wait keeps Ctrl+C responsive on Windows.
                _, pending = wait(pending, timeout=0.25)
        except BaseException:
            pool.shutdown(wait=False, cancel_futures=True)
            raise
        pool.shutdown(wait=True)
        for future in futures:
            error = future.exception()
            if error is not None:
                raise error
        self.events.emit("parallel.completed", kind=kind, tasks=len(tasks), workers=pool_size,
                         duration=time.monotonic() - started)
        return [future.result() for future in futures]

    def _exec_parallel(self, node: n.Parallel, env: Environment) -> None:
        options = self._options(node.options, env, "parallel")
        workers = options.pop("workers", None)
        if options:
            raise AgenTypeError(f"unknown parallel option(s): {', '.join(options)}")
        frames = [Environment(env) for _ in node.body]
        tasks = [lambda stmt=stmt, frame=frame: self.execute(stmt, frame)
                 for stmt, frame in zip(node.body, frames)]
        self.run_parallel(tasks, workers, "parallel")

        owners: dict[str, int] = {}
        for index, frame in enumerate(frames):
            for name in frame.vars:
                if name in owners:
                    raise AgenRuntimeError(f"parallel branches {owners[name] + 1} and {index + 1} "
                                           f"both assign '{name}'", retryable=False)
                owners[name] = index
        for frame in frames:
            env.vars.update(frame.vars)

    @staticmethod
    def _workers(value: Any) -> int:
        if value is None:
            return DEFAULT_WORKERS
        if not is_int(value) or value < 1:
            raise AgenTypeError(f"workers must be a positive integer, got {format_value(value)}")
        return value

    def cached(self, parts: Any, compute: Callable[[], Any]) -> Any:
        """Memoize an expensive call (model request) for the rest of this run."""
        key = hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()
        with self._cache_lock:
            hit = key in self._cache
            value = self._cache.get(key)
        if hit:
            self.events.emit("cache.hit", key=key[:12])
        else:
            value = compute()
            with self._cache_lock:
                self._cache[key] = value
        return copy.deepcopy(value)

    # Helpers -----------------------------------------------------------------

    def _options(self, expr: n.Expr | None, env: Environment, what: str) -> dict[str, Any]:
        if expr is None:
            return {}
        value = self.evaluate(expr, env)
        if not isinstance(value, dict):
            raise AgenTypeError(f"{what} options must be a map, got {type_name(value)}")
        return dict(value)

    def _eval_config(self, config: n.Config, env: Environment) -> dict[str, Any]:
        return {key: self.evaluate(value, env) for key, value in config}
