"""Runtime value types and the core operations on them."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any, Callable

from .errors import AgenNameError, AgenRuntimeError, AgenTypeError


class Environment:
    """A lexical scope. Assignment binds in the current scope; lookups walk outward."""

    __slots__ = ("vars", "parent")

    def __init__(self, parent: Environment | None = None) -> None:
        self.vars: dict[str, Any] = {}
        self.parent = parent

    def lookup(self, name: str) -> Any:
        env: Environment | None = self
        while env is not None:
            if name in env.vars:
                return env.vars[name]
            env = env.parent
        raise AgenNameError(f"'{name}' is not defined")

    def set(self, name: str, value: Any) -> None:
        self.vars[name] = value


class _Shared:
    """Values shared by reference: step snapshots must never deep-copy them."""

    def __deepcopy__(self, memo: dict) -> _Shared:
        return self


@dataclass(eq=False, repr=False)
class Function(_Shared):
    name: str
    params: list[str]
    body: list
    closure: Environment

    def __repr__(self) -> str:
        return f"<fn {self.name}>"


class Workflow(Function):
    def __repr__(self) -> str:
        return f"<workflow {self.name}>"


@dataclass(eq=False, repr=False)
class Class(_Shared):
    name: str
    parent: Class | None
    methods: dict[str, Function]

    def __repr__(self) -> str:
        return f"<class {self.name}>"

    def lookup_method(self, name: str) -> tuple[Function, Class] | None:
        if name in self.methods:
            return self.methods[name], self
        if self.parent is not None:
            return self.parent.lookup_method(name)
        return None


class Instance:
    """A class instance. Fields are copied on snapshot so a failed step can roll them back."""

    def __init__(self, klass: Class) -> None:
        self.klass = klass
        self.fields: dict[str, Any] = {}

    def __deepcopy__(self, memo: dict) -> Instance:
        copied = Instance(self.klass)
        memo[id(self)] = copied
        copied.fields = copy.deepcopy(self.fields, memo)
        return copied

    def __eq__(self, other: object) -> bool:
        return isinstance(other, Instance) and self.klass is other.klass and self.fields == other.fields

    def __repr__(self) -> str:
        inner = ", ".join(f"{key}: {format_value(value, True)}" for key, value in self.fields.items())
        return f"{self.klass.name}({inner})"


@dataclass(repr=False)
class BoundMethod:
    instance: Instance
    method: Function
    owner: Class

    def __repr__(self) -> str:
        return f"<bound {self.owner.name}.{self.method.name}>"


@dataclass(eq=False, repr=False)
class SuperView(_Shared):
    """``super`` seen from a method: calling it runs the parent constructor."""

    instance: Instance
    klass: Class

    def __repr__(self) -> str:
        return f"<super {self.klass.name}>"


@dataclass(eq=False, repr=False)
class Builtin(_Shared):
    name: str
    fn: Callable[..., Any]

    def __repr__(self) -> str:
        return f"<builtin {self.name}>"


@dataclass(eq=False, repr=False)
class Resource(_Shared):
    kind: str
    name: str
    config: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"<resource {self.kind} {self.name}>"


@dataclass(eq=False, repr=False)
class Agent(_Shared):
    name: str
    config: dict[str, Any] = field(default_factory=dict)

    def __repr__(self) -> str:
        return f"<agent {self.name}>"


class Module:
    """A loaded library. Attribute access reads the names it defines."""

    def __init__(self, name: str, path: str, bindings: dict[str, Any], digest: str) -> None:
        self.name = name
        self.path = path
        self.bindings = bindings
        self.digest = digest

    def __repr__(self) -> str:
        return f"<library {self.name}>"

    def __deepcopy__(self, memo: dict) -> Module:
        return self

    def member(self, name: str) -> Any:
        if name == "name":
            return self.name
        if name not in self.bindings:
            raise AgenRuntimeError(f"library '{self.name}' has no '{name}'", retryable=False)
        return self.bindings[name]


def is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if is_number(value):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return "map"
    if isinstance(value, Workflow):
        return "workflow"
    if isinstance(value, Class):
        return "class"
    if isinstance(value, Instance):
        return value.klass.name
    if isinstance(value, (Function, Builtin, BoundMethod)):
        return "fn"
    if isinstance(value, Resource):
        return "resource"
    if isinstance(value, Agent):
        return "agent"
    if type(value).__name__ == "Secret":
        return "secret"
    if type(value).__name__ == "Vault":
        return "keyvault"
    if isinstance(value, Module):
        return "library"
    return type(value).__name__


def truthy(value: Any) -> bool:
    if value is None or isinstance(value, (bool, int, float, str, list, dict)):
        return bool(value)
    return True


def format_value(value: Any, nested: bool = False) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False) if nested else value
    if isinstance(value, list):
        return "[" + ", ".join(format_value(v, True) for v in value) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(f"{k}: {format_value(v, True)}" for k, v in value.items()) + "}"
    if isinstance(value, Instance):
        return repr(value)
    return str(value)


def _equal(a: Any, b: Any) -> bool:
    if is_number(a) and is_number(b):
        return a == b
    return type(a) is type(b) and a == b


def binary_op(op: str, left: Any, right: Any) -> Any:
    if op == "+":
        if is_number(left) and is_number(right):
            return left + right
        if isinstance(left, str) or isinstance(right, str):
            return format_value(left) + format_value(right)
        if isinstance(left, list) and isinstance(right, list):
            return left + right
        raise AgenTypeError(f"cannot add {type_name(left)} and {type_name(right)}")
    if op == "==":
        return _equal(left, right)
    if op == "!=":
        return not _equal(left, right)
    if op in ("-", "*", "/", "%"):
        if not (is_number(left) and is_number(right)):
            raise AgenTypeError(f"operator '{op}' needs numbers, got {type_name(left)} and {type_name(right)}")
        if op in ("/", "%") and right == 0:
            raise AgenRuntimeError("division by zero", retryable=False)
        if op == "-":
            return left - right
        if op == "*":
            return left * right
        if op == "/":
            return left / right
        return left % right
    if op in ("<", "<=", ">", ">="):
        comparable = (is_number(left) and is_number(right)) or (isinstance(left, str) and isinstance(right, str))
        if not comparable:
            raise AgenTypeError(f"cannot compare {type_name(left)} and {type_name(right)}")
        if op == "<":
            return left < right
        if op == "<=":
            return left <= right
        if op == ">":
            return left > right
        return left >= right
    raise AgenTypeError(f"unknown operator '{op}'")


def get_index(target: Any, index: Any) -> Any:
    if isinstance(target, (list, str)):
        if not is_int(index):
            raise AgenTypeError(f"{type_name(target)} index must be an integer, got {type_name(index)}")
        if not -len(target) <= index < len(target):
            raise AgenRuntimeError(f"index {index} out of range (length {len(target)})", retryable=False)
        return target[index]
    if isinstance(target, dict):
        if not isinstance(index, str):
            raise AgenTypeError(f"map keys must be strings, got {type_name(index)}")
        if index not in target:
            raise AgenRuntimeError(f"key \"{index}\" not found in map", retryable=False)
        return target[index]
    raise AgenTypeError(f"cannot index into {type_name(target)}")


def set_index(target: Any, index: Any, value: Any) -> None:
    if isinstance(target, list):
        if not is_int(index):
            raise AgenTypeError(f"list index must be an integer, got {type_name(index)}")
        if not -len(target) <= index < len(target):
            raise AgenRuntimeError(f"index {index} out of range (length {len(target)})", retryable=False)
        target[index] = value
    elif isinstance(target, dict):
        if not isinstance(index, str):
            raise AgenTypeError(f"map keys must be strings, got {type_name(index)}")
        target[index] = value
    else:
        raise AgenTypeError(f"cannot assign into {type_name(target)}")


def set_member(target: Any, name: str, value: Any) -> None:
    if isinstance(target, Instance):
        target.fields[name] = value
        return
    if isinstance(target, dict):
        target[name] = value
        return
    raise AgenTypeError(f"cannot set attribute '{name}' on {type_name(target)}")


def get_member(target: Any, name: str) -> Any:
    if isinstance(target, Instance):
        if name in target.fields:
            return target.fields[name]
        found = target.klass.lookup_method(name)
        if found is None:
            raise AgenRuntimeError(f"{target.klass.name} has no attribute '{name}'", retryable=False)
        method, owner = found
        return BoundMethod(target, method, owner)
    if isinstance(target, SuperView):
        found = target.klass.lookup_method(name)
        if found is None:
            raise AgenRuntimeError(f"{target.klass.name} has no method '{name}'", retryable=False)
        method, owner = found
        return BoundMethod(target.instance, method, owner)
    if isinstance(target, dict):
        if name not in target:
            raise AgenRuntimeError(f"key \"{name}\" not found in map", retryable=False)
        return target[name]
    if isinstance(target, Resource):
        if name in ("name", "kind"):
            return getattr(target, name)
        if name in target.config:
            return target.config[name]
        raise AgenRuntimeError(f"resource '{target.name}' has no attribute '{name}'", retryable=False)
    if isinstance(target, Agent):
        if name == "name":
            return target.name
        if name in target.config:
            return target.config[name]
        raise AgenRuntimeError(f"agent '{target.name}' has no attribute '{name}'", retryable=False)
    if type(target).__name__ in ("Vault", "Secret") or isinstance(target, Module):
        return target.member(name)
    raise AgenTypeError(f"{type_name(target)} has no attributes")
