"""Standard library of built-in functions."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Callable

from . import agents, jev, net
from .durable import encode
from .errors import AgenRuntimeError, AgenTypeError
from .values import Agent, Builtin, Resource, format_value, is_int, is_number, type_name

if TYPE_CHECKING:
    from .interpreter import Interpreter


def _require(value: Any, check: Callable[[Any], bool], fn: str, expected: str) -> None:
    if not check(value):
        raise AgenTypeError(f"{fn}() expects {expected}, got {type_name(value)}")


def make_builtins(interp: Interpreter) -> dict[str, Builtin]:
    flaky_calls: dict[str, int] = {}
    lock = threading.Lock()

    # Core ---------------------------------------------------------------------

    def print_(*args: Any) -> None:
        line = " ".join(format_value(a) for a in args)
        with lock:
            print(line, flush=True)

    def len_(value: Any) -> int:
        _require(value, lambda v: isinstance(v, (str, list, dict)), "len", "a string, list or map")
        return len(value)

    def str_(value: Any) -> str:
        return format_value(value)

    def int_(value: Any) -> int:
        return int(value)

    def float_(value: Any) -> float:
        return float(value)

    def round_(value: Any, digits: Any = 0) -> float | int:
        _require(value, is_number, "round", "a number")
        _require(digits, is_int, "round", "an integer number of digits")
        return round(value) if digits == 0 else round(value, digits)

    def range_(start: Any, stop: Any = None) -> list[int]:
        _require(start, is_int, "range", "integers")
        if stop is None:
            return list(range(start))
        _require(stop, is_int, "range", "integers")
        return list(range(start, stop))

    def push(items: Any, value: Any) -> list:
        _require(items, lambda v: isinstance(v, list), "push", "a list")
        items.append(value)
        return items

    def keys(mapping: Any) -> list[str]:
        _require(mapping, lambda v: isinstance(v, dict), "keys", "a map")
        return list(mapping)

    def join(items: Any, sep: str = "") -> str:
        _require(items, lambda v: isinstance(v, list), "join", "a list")
        return str(sep).join(format_value(v) for v in items)

    def split(text: Any, sep: Any = None) -> list[str]:
        _require(text, lambda v: isinstance(v, str), "split", "a string")
        return text.split(sep)

    def contains(collection: Any, item: Any) -> bool:
        _require(collection, lambda v: isinstance(v, (str, list, dict)), "contains", "a string, list or map")
        return item in collection

    def upper(text: Any) -> str:
        return format_value(text).upper()

    def lower(text: Any) -> str:
        return format_value(text).lower()

    def type_(value: Any) -> str:
        return type_name(value)

    # Time & environment -------------------------------------------------------

    def sleep(seconds: Any) -> None:
        interp.sleep(seconds)

    def now() -> str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")

    def timestamp() -> float:
        return time.time()

    def env(name: Any, default: Any = None) -> Any:
        return os.environ.get(str(name), default)

    # Data & network -----------------------------------------------------------

    def json_parse(text: Any) -> Any:
        return json.loads(text)

    def json_str(value: Any) -> str:
        return json.dumps(encode(value))

    def http_get(url: Any) -> str:
        return net.request("GET", str(url), timeout=interp.remaining_time())

    def http_post(url: Any, body: Any = None) -> str:
        return net.request("POST", str(url), body=body, timeout=interp.remaining_time())

    # Agents & cloud -----------------------------------------------------------

    def ask(agent: Any, prompt: Any) -> str:
        _require(agent, lambda v: isinstance(v, Agent), "ask", "an agent")
        text = format_value(prompt)

        def call() -> str:
            return agents.ask(agent, text, timeout=interp.remaining_time())

        if agent.config.get("cache") is True:
            return interp.cached(["ask", agent.name, encode(agent.config), text], call)
        return call()

    def ask_all(agent: Any, prompts: Any, workers: Any = None) -> list[str]:
        _require(agent, lambda v: isinstance(v, Agent), "ask_all", "an agent")
        _require(prompts, lambda v: isinstance(v, list), "ask_all", "a list of prompts")
        return interp.run_parallel([lambda p=p: ask(agent, p) for p in prompts], workers, "ask_all")

    # Parallelism ----------------------------------------------------------------

    def pmap(fn: Any, items: Any, workers: Any = None) -> list[Any]:
        """Call ``fn`` on every item concurrently; results keep the input order."""
        _require(items, lambda v: isinstance(v, (list, dict, str)), "pmap", "a list")
        return interp.run_parallel([lambda item=item: interp.call(fn, [item]) for item in list(items)],
                                   workers, "pmap")

    # JEV typed questions (TypeSafe System One) ----------------------------------

    def choice(instructions: Any, criteria: Any) -> dict[str, Any]:
        return jev.question("choice", instructions, criteria)

    def score(instructions: Any, criteria: Any) -> dict[str, Any]:
        return jev.question("score", instructions, criteria)

    def noul(instructions: Any = None, criteria: Any = None) -> dict[str, Any]:
        return jev.question("noul", instructions, criteria)

    def _jev_options(options: Any) -> dict[str, Any]:
        if options is None:
            return {}
        _require(options, lambda v: isinstance(v, dict), "jev", "an options map")
        unknown = set(options) - {"model", "timeout", "cache", "workers"}
        if unknown:
            raise AgenTypeError(f"unknown jev option(s): {', '.join(sorted(unknown))}")
        return options

    def jev_(state: Any, questions: Any, options: Any = None) -> dict[str, Any]:
        opts = _jev_options(options)
        jev.validate_questions(questions)
        payload = encode(state)
        timeouts = [t for t in (opts.get("timeout"), interp.remaining_time()) if t is not None]

        def call() -> dict[str, Any]:
            return jev.system_one(payload, questions, model=opts.get("model"),
                                  timeout=min(timeouts) if timeouts else None)

        if opts.get("cache") is True:
            return interp.cached(["jev", opts.get("model"), payload, questions], call)
        return call()

    def jev_all(states: Any, questions: Any, options: Any = None) -> list[dict[str, Any]]:
        """Ask the same questions about every state concurrently, one request per state."""
        _require(states, lambda v: isinstance(v, list), "jev_all", "a list of inputs")
        opts = _jev_options(options)
        jev.validate_questions(questions)
        return interp.run_parallel([lambda s=s: jev_(s, questions, options) for s in states],
                                   opts.get("workers"), "jev_all")

    def put(resource: Any, key: Any, value: Any) -> None:
        _require(resource, lambda v: isinstance(v, Resource), "put", "a resource")
        interp.infra.put(interp.current_env, resource, key, value)

    def get(resource: Any, key: Any, default: Any = None) -> Any:
        _require(resource, lambda v: isinstance(v, Resource), "get", "a resource")
        return interp.infra.get(interp.current_env, resource, key, default)

    # Observability & fault injection ------------------------------------------

    def emit(name: Any, data: Any = None) -> None:
        interp.events.emit("custom", name=format_value(name), data=encode(data))

    def alert(message: Any) -> None:
        interp.events.alert(format_value(message))

    def fail(message: Any = "failure") -> None:
        raise AgenRuntimeError(format_value(message))

    def chaos(probability: Any) -> None:
        """Randomly inject a transient failure (use ``--seed`` for reproducible runs)."""
        _require(probability, is_number, "chaos", "a probability between 0 and 1")
        if interp.rng.random() < probability:
            raise AgenRuntimeError(f"chaos: injected failure (p={probability})")

    def flaky(name: Any, times: Any) -> None:
        """Fail the first ``times`` calls for ``name``, then succeed: a deterministic outage."""
        _require(times, is_int, "flaky", "an integer count")
        key = format_value(name)
        with lock:
            calls = flaky_calls.get(key, 0) + 1
            flaky_calls[key] = calls
        if calls <= times:
            raise AgenRuntimeError(f"flaky '{key}': simulated outage ({calls}/{times})")

    table: dict[str, Callable[..., Any]] = {
        "print": print_, "len": len_, "str": str_, "int": int_, "float": float_, "round": round_,
        "range": range_, "push": push, "keys": keys, "join": join, "split": split,
        "contains": contains, "upper": upper, "lower": lower, "type": type_,
        "sleep": sleep, "now": now, "timestamp": timestamp, "env": env,
        "json_parse": json_parse, "json_str": json_str, "http_get": http_get, "http_post": http_post,
        "ask": ask, "ask_all": ask_all, "pmap": pmap, "put": put, "get": get,
        "jev": jev_, "jev_all": jev_all, "choice": choice, "score": score, "noul": noul,
        "emit": emit, "alert": alert, "fail": fail, "chaos": chaos, "flaky": flaky,
    }
    return {name: Builtin(name, fn) for name, fn in table.items()}
