"""Recursive-descent parser producing the AST defined in :mod:`agen.nodes`."""

from __future__ import annotations

from typing import Callable

from . import nodes as n
from .errors import ParseError
from .lexer import Token, TokenType, tokenize

_COMPARISON_OPS = ("==", "!=", "<", "<=", ">", ">=")


def parse(source: str) -> list[n.Stmt]:
    return Parser(tokenize(source)).parse()


def _describe(tok: Token) -> str:
    if tok.type is TokenType.EOF:
        return "end of file"
    if tok.type is TokenType.NEWLINE:
        return "end of line"
    if tok.type is TokenType.STRING:
        return f'string "{tok.value}"'
    return f"'{tok.value}'"


class Parser:
    def __init__(self, tokens: list[Token]) -> None:
        self._tokens = tokens
        self._pos = 0
        self._keyword_statements: dict[str, Callable[[], n.Stmt]] = {
            "let": self._let,
            "fn": self._fn,
            "class": self._class,
            "workflow": self._workflow,
            "step": self._step,
            "resource": self._resource,
            "agent": self._agent,
            "service": self._service,
            "deploy": self._deploy,
            "if": self._if,
            "while": self._while,
            "for": self._for,
            "return": self._return,
        }

    # Token helpers -----------------------------------------------------------

    def _peek(self) -> Token:
        return self._tokens[self._pos]

    def _advance(self) -> Token:
        tok = self._tokens[self._pos]
        if tok.type is not TokenType.EOF:
            self._pos += 1
        return tok

    def _check(self, type_: TokenType, value: object = None) -> bool:
        tok = self._peek()
        return tok.type is type_ and (value is None or tok.value == value)

    def _check_op(self, value: str) -> bool:
        return self._check(TokenType.OP, value)

    def _check_kw(self, value: str) -> bool:
        return self._check(TokenType.KEYWORD, value)

    def _match_op(self, *values: str) -> Token | None:
        tok = self._peek()
        if tok.type is TokenType.OP and tok.value in values:
            return self._advance()
        return None

    def _match_kw(self, value: str) -> Token | None:
        return self._advance() if self._check_kw(value) else None

    def _expect_op(self, value: str) -> Token:
        if not self._check_op(value):
            raise self._error(f"expected '{value}'")
        return self._advance()

    def _expect_ident(self, what: str) -> str:
        if not self._check(TokenType.IDENT):
            raise self._error(f"expected {what}")
        return str(self._advance().value)

    def _error(self, message: str) -> ParseError:
        tok = self._peek()
        return ParseError(f"{message}, found {_describe(tok)}", tok.line)

    def _skip_newlines(self) -> None:
        while self._check(TokenType.NEWLINE) or self._check_op(";"):
            self._advance()

    def _next_significant_is_kw(self, value: str) -> bool:
        j = self._pos
        while self._tokens[j].type is TokenType.NEWLINE:
            j += 1
        tok = self._tokens[j]
        return tok.type is TokenType.KEYWORD and tok.value == value

    def _at_statement_end(self) -> bool:
        return (self._check(TokenType.NEWLINE) or self._check(TokenType.EOF)
                or self._check_op(";") or self._check_op("}"))

    # Program structure -------------------------------------------------------

    def parse(self) -> list[n.Stmt]:
        statements: list[n.Stmt] = []
        self._skip_newlines()
        while not self._check(TokenType.EOF):
            statements.append(self._statement())
            self._end_statement()
        return statements

    def _end_statement(self) -> None:
        if self._check_op("}") or self._check(TokenType.EOF):
            return
        if self._check(TokenType.NEWLINE) or self._check_op(";"):
            self._skip_newlines()
            return
        raise self._error("expected end of statement")

    def _block(self) -> n.Block:
        self._expect_op("{")
        self._skip_newlines()
        body: n.Block = []
        while not self._check_op("}"):
            if self._check(TokenType.EOF):
                raise self._error("expected '}'")
            body.append(self._statement())
            self._end_statement()
        self._advance()
        return body

    def _config_block(self) -> n.Config:
        self._expect_op("{")
        self._skip_newlines()
        entries: n.Config = []
        seen: set[str] = set()
        while not self._check_op("}"):
            line = self._peek().line
            key = self._expect_ident("attribute name")
            if key in seen:
                raise ParseError(f"duplicate attribute '{key}'", line)
            seen.add(key)
            self._expect_op("=")
            entries.append((key, self._expression()))
            if self._match_op(","):
                self._skip_newlines()
            else:
                self._end_statement()
        self._advance()
        return entries

    def _params(self) -> list[str]:
        self._expect_op("(")
        params: list[str] = []
        while not self._check_op(")"):
            line = self._peek().line
            name = self._expect_ident("parameter name")
            if name in params:
                raise ParseError(f"duplicate parameter '{name}'", line)
            params.append(name)
            if not self._match_op(","):
                break
        self._expect_op(")")
        return params

    def _with_options(self) -> n.Expr | None:
        return self._expression() if self._match_kw("with") else None

    # Statements --------------------------------------------------------------

    def _statement(self) -> n.Stmt:
        tok = self._peek()
        if tok.type is TokenType.KEYWORD and tok.value in self._keyword_statements:
            return self._keyword_statements[str(tok.value)]()
        expr = self._expression()
        if self._match_op("="):
            if not isinstance(expr, (n.Name, n.Index, n.Member)):
                raise ParseError("invalid assignment target", tok.line)
            return n.Assign(expr, self._expression(), line=tok.line)
        return n.ExprStmt(expr, line=tok.line)

    def _let(self) -> n.Stmt:
        line = self._advance().line
        name = self._expect_ident("variable name")
        self._expect_op("=")
        return n.Let(name, self._expression(), line=line)

    def _fn(self) -> n.FnDef:
        line = self._advance().line
        name = self._expect_ident("function name")
        return n.FnDef(name, self._params(), self._block(), line=line)

    def _class(self) -> n.Stmt:
        line = self._advance().line
        name = self._expect_ident("class name")
        parent = self._expect_ident("parent class name") if self._match_kw("extends") else None
        self._expect_op("{")
        self._skip_newlines()
        methods: list[n.FnDef] = []
        seen: set[str] = set()
        while not self._check_op("}"):
            if self._check(TokenType.EOF):
                raise self._error("expected '}'")
            method_line = self._peek().line
            if self._match_kw("init"):
                method = n.FnDef("init", self._params(), self._block(), line=method_line)
            elif self._check_kw("fn"):
                method = self._fn()
            else:
                raise self._error("expected 'fn' or 'init' in a class body")
            if method.name in seen:
                raise ParseError(f"duplicate method '{method.name}'", method_line)
            seen.add(method.name)
            methods.append(method)
            self._end_statement()
        self._advance()
        return n.ClassDef(name, parent, methods, line=line)

    def _workflow(self) -> n.Stmt:
        line = self._advance().line
        name = self._expect_ident("workflow name")
        return n.WorkflowDef(name, self._params(), self._block(), line=line)

    def _step(self) -> n.Stmt:
        line = self._advance().line
        name = self._expect_ident("step name")
        options = self._with_options()
        body = self._block()
        fallback = None
        if self._next_significant_is_kw("fallback"):
            self._skip_newlines()
            self._advance()
            fallback = self._block()
        return n.Step(name, options, body, fallback, line=line)

    def _resource(self) -> n.Stmt:
        line = self._advance().line
        kind = self._expect_ident("resource kind (e.g. storage)")
        name = self._expect_ident("resource name")
        return n.ResourceDef(kind, name, self._config_block(), line=line)

    def _agent(self) -> n.Stmt:
        line = self._advance().line
        name = self._expect_ident("agent name")
        return n.AgentDef(name, self._config_block(), line=line)

    def _service(self) -> n.Stmt:
        line = self._advance().line
        name = self._expect_ident("service name")
        options = self._with_options()
        return n.Service(name, options, self._block(), line=line)

    def _deploy(self) -> n.Stmt:
        line = self._advance().line
        name = self._expect_ident("environment name")
        options = self._with_options()
        return n.Deploy(name, options, self._block(), line=line)

    def _if(self) -> n.Stmt:
        line = self._advance().line
        condition = self._expression()
        then_body = self._block()
        else_body = None
        if self._next_significant_is_kw("else"):
            self._skip_newlines()
            self._advance()
            else_body = [self._if()] if self._check_kw("if") else self._block()
        return n.If(condition, then_body, else_body, line=line)

    def _while(self) -> n.Stmt:
        line = self._advance().line
        return n.While(self._expression(), self._block(), line=line)

    def _for(self) -> n.Stmt:
        line = self._advance().line
        var = self._expect_ident("loop variable")
        if not self._match_kw("in"):
            raise self._error("expected 'in'")
        return n.For(var, self._expression(), self._block(), line=line)

    def _return(self) -> n.Stmt:
        line = self._advance().line
        value = None if self._at_statement_end() else self._expression()
        return n.Return(value, line=line)

    # Expressions (lowest to highest precedence) ------------------------------

    def _expression(self) -> n.Expr:
        return self._or()

    def _or(self) -> n.Expr:
        left = self._and()
        while tok := self._match_kw("or"):
            left = n.Logical("or", left, self._and(), line=tok.line)
        return left

    def _and(self) -> n.Expr:
        left = self._not()
        while tok := self._match_kw("and"):
            left = n.Logical("and", left, self._not(), line=tok.line)
        return left

    def _not(self) -> n.Expr:
        if tok := self._match_kw("not"):
            return n.Unary("not", self._not(), line=tok.line)
        return self._comparison()

    def _comparison(self) -> n.Expr:
        left = self._additive()
        while tok := self._match_op(*_COMPARISON_OPS):
            left = n.Binary(str(tok.value), left, self._additive(), line=tok.line)
        return left

    def _additive(self) -> n.Expr:
        left = self._multiplicative()
        while tok := self._match_op("+", "-"):
            left = n.Binary(str(tok.value), left, self._multiplicative(), line=tok.line)
        return left

    def _multiplicative(self) -> n.Expr:
        left = self._unary()
        while tok := self._match_op("*", "/", "%"):
            left = n.Binary(str(tok.value), left, self._unary(), line=tok.line)
        return left

    def _unary(self) -> n.Expr:
        if tok := self._match_op("-"):
            return n.Unary("-", self._unary(), line=tok.line)
        return self._postfix()

    def _postfix(self) -> n.Expr:
        expr = self._primary()
        while True:
            if tok := self._match_op("("):
                args: list[n.Expr] = []
                while not self._check_op(")"):
                    args.append(self._expression())
                    if not self._match_op(","):
                        break
                self._expect_op(")")
                expr = n.Call(expr, args, line=tok.line)
            elif tok := self._match_op("["):
                index = self._expression()
                self._expect_op("]")
                expr = n.Index(expr, index, line=tok.line)
            elif tok := self._match_op("."):
                expr = n.Member(expr, self._expect_ident("attribute name"), line=tok.line)
            else:
                return expr

    def _primary(self) -> n.Expr:
        tok = self._peek()
        if tok.type in (TokenType.NUMBER, TokenType.STRING):
            self._advance()
            return n.Literal(tok.value, line=tok.line)
        if tok.type is TokenType.KEYWORD and tok.value in ("true", "false", "null"):
            self._advance()
            return n.Literal({"true": True, "false": False, "null": None}[str(tok.value)], line=tok.line)
        if tok.type is TokenType.KEYWORD and tok.value == "self":
            self._advance()
            return n.Name("self", line=tok.line)
        if tok.type is TokenType.KEYWORD and tok.value == "super":
            self._advance()
            return n.Super(line=tok.line)
        if tok.type is TokenType.IDENT:
            self._advance()
            return n.Name(str(tok.value), line=tok.line)
        if self._match_op("("):
            expr = self._expression()
            self._expect_op(")")
            return expr
        if self._match_op("["):
            items: list[n.Expr] = []
            while not self._check_op("]"):
                items.append(self._expression())
                if not self._match_op(","):
                    break
            self._expect_op("]")
            return n.ListLit(items, line=tok.line)
        if self._match_op("{"):
            return self._map_literal(tok.line)
        raise self._error("expected an expression")

    def _map_literal(self, line: int) -> n.Expr:
        entries: list[tuple[str, n.Expr]] = []
        seen: set[str] = set()
        self._skip_newlines()
        while not self._check_op("}"):
            key_tok = self._peek()
            if key_tok.type not in (TokenType.IDENT, TokenType.STRING):
                raise self._error("expected a map key")
            self._advance()
            key = str(key_tok.value)
            if key in seen:
                raise ParseError(f"duplicate map key '{key}'", key_tok.line)
            seen.add(key)
            self._expect_op(":")
            self._skip_newlines()
            entries.append((key, self._expression()))
            self._skip_newlines()
            if not self._match_op(","):
                break
            self._skip_newlines()
        self._expect_op("}")
        return n.MapLit(entries, line=line)
