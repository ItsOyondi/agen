"""Native backend: compiles pure Agen functions to bytecode for the C++ VM.

A function is compiled the first time it is called if its body only uses
numbers, strings, booleans, null, lists, ``if``/``while``/``for``, and calls to
``len range int float str round`` or other compilable top-level functions.
Anything else (maps, objects, agents, I/O, steps, closures) stays on the
tree-walking interpreter.

The VM runs without the GIL, so ``pmap`` over a native function uses several
CPU cores. When it meets something only Python can do exactly (an error,
64-bit overflow, float formatting) it bails out and the interpreter reruns the
call. Compiled functions have no side effects, so rerunning is always safe.

Set ``AGEN_NATIVE=0`` or pass ``agen run --no-native`` to turn it off.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

from . import nodes as n
from .errors import AgenTimeoutError
from .values import Builtin, Function

if TYPE_CHECKING:
    from .interpreter import Interpreter

try:
    from . import _native
except ImportError:
    _native = None

OPS = {name: code for code, name in enumerate(
    "CONST LOAD STORE POP ADD SUB MUL DIV MOD EQ NE LT LE GT GE NEG NOT "
    "JUMP JUMP_IF_FALSE AND_JUMP OR_JUMP CALL BUILTIN INDEX LIST RETURN "
    "ITER_PREP ITER_NEXT RANGE_PREP RANGE_NEXT CMP_JUMP ADD_LOCAL "
    "BINOP_LL BINOP_LK CMP_JUMP_LL CMP_JUMP_LK CMP_JUMP_SL CMP_JUMP_SK "
    "BINOP_SL BINOP_SK ADD_LOCAL_S".split())}
# Stack effect of each instruction; CALL, BUILTIN and LIST depend on their operands.
_EFFECT = {"CONST": 1, "LOAD": 1, "STORE": -1, "POP": -1, "NEG": 0, "NOT": 0, "JUMP": 0,
           "JUMP_IF_FALSE": -1, "AND_JUMP": -1, "OR_JUMP": -1, "INDEX": -1, "RETURN": -1,
           "ITER_PREP": -1, "ITER_NEXT": 0, "RANGE_PREP": -2, "RANGE_NEXT": 0, "CMP_JUMP": -2,
           "ADD_LOCAL": 0, "BINOP_LL": 1, "BINOP_LK": 1, "CMP_JUMP_LL": 0, "CMP_JUMP_LK": 0,
           "CMP_JUMP_SL": -1, "CMP_JUMP_SK": -1, "BINOP_SL": 0, "BINOP_SK": 0, "ADD_LOCAL_S": -1, **{op: -1 for op in ("ADD", "SUB", "MUL", "DIV", "MOD", "EQ", "NE", "LT", "LE",
                                               "GT", "GE")}}
_COMPARE = frozenset({"==", "!=", "<", "<=", ">", ">="})
BUILTINS = {"len": 0, "range": 1, "int": 2, "float": 3, "str": 4, "round": 5}
_BINARY = {"+": "ADD", "-": "SUB", "*": "MUL", "/": "DIV", "%": "MOD",
           "==": "EQ", "!=": "NE", "<": "LT", "<=": "LE", ">": "GT", ">=": "GE"}
_INT64 = (-(2 ** 63), 2 ** 63 - 1)
# A function that bails this many times without ever finishing natively stays in Python.
_GIVE_UP_AFTER = 8

FALLBACK = object()


def available() -> bool:
    return _native is not None


def status() -> str:
    if _native is None:
        return "off (C++ extension not built)"
    if os.environ.get("AGEN_NATIVE", "1") == "0":
        return "off (AGEN_NATIVE=0)"
    return "on (C++ VM)"


class _Ineligible(Exception):
    pass


@dataclass
class _Entry:
    program: Any
    guards: list[tuple[str, Any]]
    successes: int = 0
    bailouts: int = 0


@dataclass
class _Unit:
    """Functions compiled together; index 0 is the entry point."""

    order: list[Function] = field(default_factory=list)
    index: dict[Function, int] = field(default_factory=dict)
    guards: dict[str, Any] = field(default_factory=dict)

    def add(self, fn: Function) -> int:
        if fn not in self.index:
            self.index[fn] = len(self.order)
            self.order.append(fn)
        return self.index[fn]


class NativeBackend:
    def __init__(self, interp: Interpreter) -> None:
        self.globals = interp.globals
        self._builtins = {name: interp.globals.vars[name] for name in BUILTINS}
        self._entries: dict[Function, _Entry | None] = {}
        self._lock = threading.Lock()
        self._local = threading.local()
        self.calls = 0
        self.fallbacks = 0

    @classmethod
    def create(cls, interp: Interpreter, enabled: bool) -> NativeBackend | None:
        if not enabled or _native is None or os.environ.get("AGEN_NATIVE", "1") == "0":
            return None
        return cls(interp)

    def call(self, fn: Function, args: list[Any], budget: float | None, slow: Callable[[], Any]) -> Any:
        """Run ``fn`` on the VM, or through ``slow`` (the Python interpreter) when it can't."""
        suppressed: set[Function] = self._local.__dict__.setdefault("suppressed", set())
        if fn in suppressed:
            return slow()
        entry = self._entries.get(fn, FALLBACK)
        if entry is FALLBACK:
            with self._lock:
                entry = self._entries[fn] = self._compile(fn)
        if entry is None or any(self.globals.vars.get(name) is not value for name, value in entry.guards):
            return slow()
        try:
            result = entry.program.run(0, tuple(args), -1.0 if budget is None else budget)
        except _native.Bailout:
            entry.bailouts += 1
            self.fallbacks += 1
            if entry.successes == 0 and entry.bailouts >= _GIVE_UP_AFTER:
                self._entries[fn] = None
            # Recursive calls made by the Python rerun would bail the same way.
            suppressed.add(fn)
            try:
                return slow()
            finally:
                suppressed.discard(fn)
        except TimeoutError:
            raise AgenTimeoutError("timed out") from None
        entry.successes += 1
        self.calls += 1
        return result

    def _compile(self, root: Function) -> _Entry | None:
        if root.closure is not self.globals:
            return None
        unit = _Unit()
        unit.add(root)
        specs = []
        try:
            position = 0
            while position < len(unit.order):
                fn = unit.order[position]
                if fn.closure is not self.globals:
                    raise _Ineligible(f"{fn.name} is a closure")
                specs.append(_FunctionCompiler(fn, unit, self).compile())
                position += 1
            program = _native.build(specs)
        except (_Ineligible, ValueError, RecursionError):
            return None
        return _Entry(program, list(unit.guards.items()))

    def resolve(self, name: str, unit: _Unit) -> tuple[str, int]:
        """A free name used as a callee: ("call", index) or ("builtin", id)."""
        value = self.globals.vars.get(name)
        if isinstance(value, Function):
            unit.guards[name] = value
            return "call", unit.add(value)
        if isinstance(value, Builtin) and value is self._builtins.get(name):
            unit.guards[name] = value
            return "builtin", BUILTINS[name]
        raise _Ineligible(f"'{name}' cannot be called natively")


def _assigned_names(body: n.Block) -> list[str]:
    names: list[str] = []

    def visit(block: n.Block) -> None:
        for stmt in block:
            if isinstance(stmt, n.Let):
                names.append(stmt.name)
            elif isinstance(stmt, n.Assign) and isinstance(stmt.target, n.Name):
                names.append(stmt.target.id)
            elif isinstance(stmt, n.For):
                names.append(stmt.var)
                visit(stmt.body)
            elif isinstance(stmt, n.While):
                visit(stmt.body)
            elif isinstance(stmt, n.If):
                visit(stmt.then_body)
                if stmt.else_body is not None:
                    visit(stmt.else_body)

    visit(body)
    return names


class _FunctionCompiler:
    def __init__(self, fn: Function, unit: _Unit, backend: NativeBackend) -> None:
        self.fn = fn
        self.unit = unit
        self.backend = backend
        self.code: list[int] = []
        self.consts: list[Any] = []
        self._const_index: dict[tuple[type, Any], int] = {}
        self.slots: dict[str, int] = {}
        for name in [*fn.params, *_assigned_names(fn.body)]:
            self.slots.setdefault(name, len(self.slots))
        self._hidden = 0
        self._depth = 0
        self._max_depth = 0

    def compile(self) -> tuple[str, int, int, int, list[int], list[Any]]:
        self.block(self.fn.body)
        self.emit("CONST", self.const(None))
        self.emit("RETURN")
        nlocals = len(self.slots) + self._hidden
        return self.fn.name, len(self.fn.params), nlocals, self._max_depth, self.code, self.consts

    # Emission helpers ------------------------------------------------------------

    def emit(self, op: str, *operands: int) -> int:
        """Append an instruction and return the position of its opcode."""
        at = len(self.code)
        self.code.append(OPS[op])
        self.code.extend(operands)
        if op in ("CALL", "BUILTIN"):
            self._depth += 1 - operands[1]
        elif op == "LIST":
            self._depth += 1 - operands[0]
        else:
            self._depth += _EFFECT[op]
        self._max_depth = max(self._max_depth, self._depth)
        return at

    def patch(self, at: int) -> None:
        self.code[at] = len(self.code)

    def const(self, value: Any) -> int:
        key = (type(value), value)
        if key not in self._const_index:
            self._const_index[key] = len(self.consts)
            self.consts.append(value)
        return self._const_index[key]

    def hidden_slot(self) -> int:
        slot = len(self.slots) + self._hidden
        self._hidden += 1
        return slot

    # Statements -------------------------------------------------------------------

    def block(self, body: n.Block) -> None:
        for stmt in body:
            self.statement(stmt)

    def statement(self, stmt: n.Stmt) -> None:
        if isinstance(stmt, n.Let):
            self.expr(stmt.value)
            self.emit("STORE", self.slots[stmt.name])
        elif isinstance(stmt, n.Assign):
            if not isinstance(stmt.target, n.Name) or stmt.target.id == "self":
                raise _Ineligible("only plain variables can be assigned natively")
            slot = self.slots[stmt.target.id]
            value = stmt.value
            if (isinstance(value, n.Binary) and value.op == "+" and isinstance(value.left, n.Name)
                    and value.left.id == stmt.target.id):
                # x = x + y: the VM appends in place when x is the only reference.
                right = self.operand(value.right)
                if right and right[0] == "K":
                    self.emit("ADD_LOCAL", slot, right[1])
                else:
                    self.expr(value.right)
                    self.emit("ADD_LOCAL_S", slot)
                return
            self.expr(value)
            self.emit("STORE", slot)
        elif isinstance(stmt, n.ExprStmt):
            self.expr(stmt.expr)
            self.emit("POP")
        elif isinstance(stmt, n.If):
            skip_then = self.jump_if_false(stmt.condition)
            self.block(stmt.then_body)
            if stmt.else_body is None:
                self.patch(skip_then)
            else:
                skip_else = self.emit("JUMP", 0) + 1
                self.patch(skip_then)
                self.block(stmt.else_body)
                self.patch(skip_else)
        elif isinstance(stmt, n.While):
            top = len(self.code)
            exit_jump = self.jump_if_false(stmt.condition)
            self.block(stmt.body)
            self.emit("JUMP", top)
            self.patch(exit_jump)
        elif isinstance(stmt, n.For):
            self.for_loop(stmt)
        elif isinstance(stmt, n.Return):
            if stmt.value is None:
                self.emit("CONST", self.const(None))
            else:
                self.expr(stmt.value)
            self.emit("RETURN")
        else:
            raise _Ineligible(f"{type(stmt).__name__} is not supported natively")

    def for_loop(self, stmt: n.For) -> None:
        first, second = self.hidden_slot(), self.hidden_slot()
        target = stmt.iterable
        is_range = (isinstance(target, n.Call) and isinstance(target.callee, n.Name)
                    and target.callee.id == "range" and target.callee.id not in self.slots
                    and len(target.args) in (1, 2)
                    and self.backend.resolve("range", self.unit) == ("builtin", BUILTINS["range"]))
        if is_range:
            # Count without building the list that range() would return.
            if len(target.args) == 1:
                self.emit("CONST", self.const(0))
            for arg in target.args:
                self.expr(arg)
            self.emit("RANGE_PREP", first, second)
            top = len(self.code)
            exit_jump = self.emit("RANGE_NEXT", first, second, self.slots[stmt.var], 0) + 4
        else:
            self.expr(target)
            self.emit("ITER_PREP", first, second)
            top = len(self.code)
            exit_jump = self.emit("ITER_NEXT", first, second, self.slots[stmt.var], 0) + 4
        self.block(stmt.body)
        self.emit("JUMP", top)
        self.patch(exit_jump)

    def jump_if_false(self, condition: n.Expr) -> int:
        """Emit a conditional jump and return the operand position to patch."""
        if not (isinstance(condition, n.Binary) and condition.op in _COMPARE):
            self.expr(condition)
            return self.emit("JUMP_IF_FALSE", 0) + 1
        op = OPS[_BINARY[condition.op]]
        left, right = self.operand(condition.left), self.operand(condition.right)
        if left and left[0] == "L" and right:
            return self.emit(f"CMP_JUMP_L{right[0]}", op, left[1], right[1], 0) + 4
        self.expr(condition.left)
        if right:
            return self.emit(f"CMP_JUMP_S{right[0]}", op, right[1], 0) + 3
        self.expr(condition.right)
        return self.emit("CMP_JUMP", op, 0) + 2

    def operand(self, node: n.Expr) -> tuple[str, int] | None:
        """("L", slot) for a local, ("K", index) for a constant, None if it needs the stack."""
        if isinstance(node, n.Name) and node.id in self.slots:
            return "L", self.slots[node.id]
        if isinstance(node, n.Literal) and (node.value is None or isinstance(node.value, (bool, float, str))
                                            or (isinstance(node.value, int)
                                                and _INT64[0] <= node.value <= _INT64[1])):
            return "K", self.const(node.value)
        return None

    # Expressions ------------------------------------------------------------------

    def expr(self, node: n.Expr) -> None:
        if isinstance(node, n.Literal):
            value = node.value
            if isinstance(value, int) and not isinstance(value, bool) and not _INT64[0] <= value <= _INT64[1]:
                raise _Ineligible("integer literal does not fit 64 bits")
            if value is not None and not isinstance(value, (bool, int, float, str)):
                raise _Ineligible("unsupported literal")
            self.emit("CONST", self.const(value))
        elif isinstance(node, n.Name):
            if node.id not in self.slots:
                raise _Ineligible(f"'{node.id}' is not a local variable")
            self.emit("LOAD", self.slots[node.id])
        elif isinstance(node, n.ListLit):
            for item in node.items:
                self.expr(item)
            self.emit("LIST", len(node.items))
        elif isinstance(node, n.Unary):
            self.expr(node.operand)
            self.emit("NOT" if node.op == "not" else "NEG")
        elif isinstance(node, n.Binary):
            if node.op not in _BINARY:
                raise _Ineligible(f"operator {node.op}")
            left, right = self.operand(node.left), self.operand(node.right)
            if left and left[0] == "L" and right:
                self.emit(f"BINOP_L{right[0]}", OPS[_BINARY[node.op]], left[1], right[1])
                return
            if right:
                self.expr(node.left)
                self.emit(f"BINOP_S{right[0]}", OPS[_BINARY[node.op]], right[1])
                return
            self.expr(node.left)
            self.expr(node.right)
            self.emit(_BINARY[node.op])
        elif isinstance(node, n.Logical):
            self.expr(node.left)
            end = self.emit("AND_JUMP" if node.op == "and" else "OR_JUMP", 0) + 1
            self.expr(node.right)
            self.patch(end)
        elif isinstance(node, n.Call):
            self.call(node)
        elif isinstance(node, n.Index):
            self.expr(node.target)
            self.expr(node.index)
            self.emit("INDEX")
        else:
            raise _Ineligible(f"{type(node).__name__} is not supported natively")

    def call(self, node: n.Call) -> None:
        callee = node.callee
        if not isinstance(callee, n.Name) or callee.id in self.slots:
            raise _Ineligible("only named functions can be called natively")
        kind, target = self.backend.resolve(callee.id, self.unit)
        for arg in node.args:
            self.expr(arg)
        self.emit("CALL" if kind == "call" else "BUILTIN", target, len(node.args))
