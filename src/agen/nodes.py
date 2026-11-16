"""Abstract syntax tree for Agen programs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Node:
    line: int = field(default=0, kw_only=True)


class Expr(Node):
    pass


class Stmt(Node):
    pass


Block = list[Stmt]
Config = list[tuple[str, Expr]]


# Expressions -----------------------------------------------------------------

@dataclass
class Literal(Expr):
    value: Any


@dataclass
class Name(Expr):
    id: str


@dataclass
class ListLit(Expr):
    items: list[Expr]


@dataclass
class MapLit(Expr):
    entries: list[tuple[str, Expr]]


@dataclass
class Unary(Expr):
    op: str
    operand: Expr


@dataclass
class Binary(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass
class Logical(Expr):
    op: str
    left: Expr
    right: Expr


@dataclass
class Call(Expr):
    callee: Expr
    args: list[Expr]


@dataclass
class Index(Expr):
    target: Expr
    index: Expr


@dataclass
class Member(Expr):
    target: Expr
    name: str


# General statements ----------------------------------------------------------

@dataclass
class Let(Stmt):
    name: str
    value: Expr


@dataclass
class Assign(Stmt):
    target: Expr
    value: Expr


@dataclass
class ExprStmt(Stmt):
    expr: Expr


@dataclass
class If(Stmt):
    condition: Expr
    then_body: Block
    else_body: Block | None


@dataclass
class While(Stmt):
    condition: Expr
    body: Block


@dataclass
class For(Stmt):
    var: str
    iterable: Expr
    body: Block


@dataclass
class Return(Stmt):
    value: Expr | None


@dataclass
class FnDef(Stmt):
    name: str
    params: list[str]
    body: Block


@dataclass
class ClassDef(Stmt):
    name: str
    parent: str | None
    methods: list[FnDef]


@dataclass
class Super(Expr):
    """``super`` as it appears in a method: ``super(args)`` or ``super.method``."""


# Agentic / cloud statements --------------------------------------------------

@dataclass
class WorkflowDef(Stmt):
    name: str
    params: list[str]
    body: Block


@dataclass
class Step(Stmt):
    name: str
    options: Expr | None
    body: Block
    fallback: Block | None


@dataclass
class ResourceDef(Stmt):
    kind: str
    name: str
    config: Config


@dataclass
class AgentDef(Stmt):
    name: str
    config: Config


@dataclass
class Service(Stmt):
    name: str
    options: Expr | None
    body: Block


@dataclass
class Deploy(Stmt):
    name: str
    options: Expr | None
    body: Block


@dataclass
class Parallel(Stmt):
    """Each top-level statement of ``body`` runs concurrently in its own scope."""

    options: Expr | None
    body: Block
