"""Command-line interface: ``agen run | plan | check | status | export``."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, native
from . import nodes as n
from .dotenv import load_dotenv
from .durable import JournalStore
from .errors import AgenError, AgenSyntaxError
from .events import EventLog
from .infra import InfraManager
from .interpreter import Interpreter, RuntimeOptions
from .parser import parse
from .terraform import to_terraform


def _state_dir(args: argparse.Namespace) -> Path:
    return args.state_dir or args.file.resolve().parent / ".agen"


def _load(path: Path) -> list[n.Stmt]:
    return parse(path.read_text(encoding="utf-8"))


def cmd_run(args: argparse.Namespace) -> int:
    program = _load(args.file)
    interp = Interpreter(RuntimeOptions(state_dir=_state_dir(args), crash_after=args.crash_after,
                                        seed=args.seed, quiet=args.quiet, native=not args.no_native,
                                        source_path=args.file.resolve()))
    program_dir = args.file.resolve().parent
    load_dotenv(program_dir / ".env")
    interp.secrets.activate_file(program_dir)
    if args.fresh:
        interp.events.emit("runtime.fresh", removed=interp.journal.clear())
    try:
        interp.run(program)
    finally:
        interp.events.info(interp.events.summary())
    return 0


def cmd_plan(args: argparse.Namespace) -> int:
    program = _load(args.file)
    state_dir = _state_dir(args)
    events = EventLog(state_dir / "events.jsonl", stream=sys.stdout)
    Interpreter(RuntimeOptions(state_dir=state_dir, mode="plan", source_path=args.file.resolve()),
                events=events).run(program)
    if not any(isinstance(stmt, n.Deploy) for stmt in program):
        print("no deploy blocks found")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    program = _load(args.file)
    print(f"{args.file}: ok ({len(program)} top-level statement(s))")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    state_dir = _state_dir(args)
    runs = JournalStore(state_dir / "runs").runs()
    print("workflow runs:" if runs else "workflow runs: none")
    for run in runs:
        done = sum(1 for s in run["steps"].values() if s["status"] in ("completed", "fallback"))
        failed = [k for k, s in run["steps"].items() if s["status"] == "failed"]
        line = f"  {run['run_id']:<32} {run['status']:<12} {done} step(s) done"
        if failed:
            line += f", failed: {', '.join(failed)}"
        print(line)

    environments = InfraManager(state_dir).environments()
    print("environments:" if environments else "environments: none")
    for env, state in environments.items():
        resources = ", ".join(f"{name} [{r['kind']}]" for name, r in state["resources"].items()) or "empty"
        print(f"  {env} ({state.get('provider')}): {resources}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    program = _load(args.file)
    interp = Interpreter(RuntimeOptions(state_dir=_state_dir(args), mode="declare",
                                        source_path=args.file.resolve()),
                         events=EventLog(None, quiet=True))
    interp.run(program)
    document, warnings = to_terraform(interp.infra.declared.values())
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    text = json.dumps(document, indent=2)
    if args.output:
        args.output.write_text(text + "\n", encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        print(text)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agen",
        description="Agen: infrastructure, deployment and fault-tolerant application logic in one language.",
    )
    parser.add_argument("--version", action="version",
                        version=f"agen {__version__} (native backend: {native.status()})")
    sub = parser.add_subparsers(dest="command", required=True)

    def command(name: str, help_text: str, handler) -> argparse.ArgumentParser:
        p = sub.add_parser(name, help=help_text, description=help_text)
        p.add_argument("file", type=Path, help="path to an .agn program")
        p.add_argument("--state-dir", type=Path, help="where checkpoints and infra state live "
                                                      "(default: .agen next to the program)")
        p.set_defaults(handler=handler)
        return p

    run = command("run", "run a program; interrupted workflows resume automatically", cmd_run)
    run.add_argument("--fresh", action="store_true", help="discard saved workflow checkpoints first")
    run.add_argument("--crash-after", metavar="STEP", help="simulate a process crash after STEP is checkpointed")
    run.add_argument("--seed", type=int, help="seed for chaos() and backoff jitter")
    run.add_argument("-q", "--quiet", action="store_true", help="only print program output and alerts")
    run.add_argument("--no-native", action="store_true", help="run everything on the Python interpreter")

    command("plan", "show infrastructure changes without applying them", cmd_plan)
    command("check", "check a program for syntax errors", cmd_check)
    command("status", "show workflow checkpoints and deployed environments", cmd_status)
    export = command("export", "export declared resources as Terraform JSON (Azure)", cmd_export)
    export.add_argument("-o", "--output", type=Path, help="write to a file, e.g. main.tf.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.handler(args)
    except AgenSyntaxError as exc:
        print(f"{args.file}: syntax error: {exc}", file=sys.stderr)
        return 2
    except AgenError as exc:
        print(f"{args.file}: error: {exc}", file=sys.stderr)
        return 1
    except OSError as exc:
        print(f"{args.file}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    except RecursionError:
        print(f"{args.file}: error: maximum recursion depth exceeded", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("interrupted: progress is checkpointed, run again to resume", file=sys.stderr)
        return 130
