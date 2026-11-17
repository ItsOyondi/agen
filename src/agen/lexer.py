"""Tokenizer for Agen source code."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from .errors import LexError


class TokenType(Enum):
    NUMBER = "number"
    STRING = "string"
    IDENT = "identifier"
    KEYWORD = "keyword"
    AT = "directive"
    OP = "operator"
    NEWLINE = "newline"
    EOF = "end of file"


KEYWORDS = frozenset({
    "let", "fn", "return", "if", "else", "while", "for", "in",
    "and", "or", "not", "true", "false", "null",
    "class", "extends", "init", "self", "super",
    "workflow", "step", "fallback", "with",
    "resource", "agent", "service", "deploy", "parallel",
})

_AT_WORDS = frozenset({"import", "at", "imported"})
_TWO_CHAR_OPS = frozenset({"==", "!=", "<=", ">="})
_ONE_CHAR_OPS = frozenset("+-*/%<>=()[]{},:.;")
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


@dataclass(frozen=True)
class Token:
    type: TokenType
    value: object
    line: int
    col: int


def _is_digit(ch: str) -> bool:
    return "0" <= ch <= "9"


def _is_ident_start(ch: str) -> bool:
    return ch == "_" or ("a" <= ch <= "z") or ("A" <= ch <= "Z")


def tokenize(source: str) -> list[Token]:
    """Convert source text into tokens.

    Newlines terminate statements, except inside ``(...)`` and ``[...]`` so
    long argument lists and list literals can span several lines.
    """
    tokens: list[Token] = []
    i, line, line_start, depth = 0, 1, 0, 0
    n = len(source)

    while i < n:
        ch = source[i]
        col = i - line_start + 1

        if ch == "\n":
            if depth == 0:
                tokens.append(Token(TokenType.NEWLINE, "\n", line, col))
            i += 1
            line += 1
            line_start = i
            continue
        if ch in " \t\r":
            i += 1
            continue
        if ch == "#":
            while i < n and source[i] != "\n":
                i += 1
            continue

        if _is_digit(ch):
            start = i
            while i < n and _is_digit(source[i]):
                i += 1
            if i + 1 < n and source[i] == "." and _is_digit(source[i + 1]):
                i += 1
                while i < n and _is_digit(source[i]):
                    i += 1
                value: object = float(source[start:i])
            else:
                value = int(source[start:i])
            tokens.append(Token(TokenType.NUMBER, value, line, col))
            continue

        if _is_ident_start(ch):
            start = i
            while i < n and (_is_ident_start(source[i]) or _is_digit(source[i])):
                i += 1
            word = source[start:i]
            kind = TokenType.KEYWORD if word in KEYWORDS else TokenType.IDENT
            tokens.append(Token(kind, word, line, col))
            continue

        if ch == "@":
            i += 1
            if i >= n or not _is_ident_start(source[i]):
                raise LexError("expected a name after '@'", line)
            start = i
            while i < n and (_is_ident_start(source[i]) or _is_digit(source[i])):
                i += 1
            word = source[start:i]
            if word not in _AT_WORDS:
                raise LexError(f"unknown directive '@{word}'. Use @import, @at, or @imported", line)
            tokens.append(Token(TokenType.AT, word, line, col))
            continue

        if ch == '"':
            i += 1
            chars: list[str] = []
            while True:
                if i >= n or source[i] == "\n":
                    raise LexError("unterminated string literal", line)
                c = source[i]
                if c == '"':
                    i += 1
                    break
                if c == "\\":
                    esc = source[i + 1] if i + 1 < n else ""
                    if esc not in _ESCAPES:
                        raise LexError(f"invalid escape sequence '\\{esc}'", line)
                    chars.append(_ESCAPES[esc])
                    i += 2
                    continue
                chars.append(c)
                i += 1
            tokens.append(Token(TokenType.STRING, "".join(chars), line, col))
            continue

        two = source[i:i + 2]
        if two in _TWO_CHAR_OPS:
            tokens.append(Token(TokenType.OP, two, line, col))
            i += 2
            continue
        if ch in _ONE_CHAR_OPS:
            if ch in "([":
                depth += 1
            elif ch in ")]":
                depth = max(0, depth - 1)
            tokens.append(Token(TokenType.OP, ch, line, col))
            i += 1
            continue

        raise LexError(f"unexpected character {ch!r}", line)

    tokens.append(Token(TokenType.NEWLINE, "\n", line, 1))
    tokens.append(Token(TokenType.EOF, None, line, 1))
    return tokens
