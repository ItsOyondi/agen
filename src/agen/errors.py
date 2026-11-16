"""Error hierarchy.

Every error carries a ``retryable`` flag. The runtime retries retryable errors
(network failures, timeouts, provider outages, explicit ``fail()``) and fails
fast on deterministic ones (syntax, unknown names, type errors), because
retrying a programming mistake only delays the inevitable.
"""

from __future__ import annotations


class AgenError(Exception):
    retryable = True

    def __init__(self, message: str, line: int | None = None, *, retryable: bool | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.line = line
        if retryable is not None:
            self.retryable = retryable

    def __str__(self) -> str:
        return f"line {self.line}: {self.message}" if self.line else self.message


class AgenSyntaxError(AgenError):
    retryable = False


class LexError(AgenSyntaxError):
    pass


class ParseError(AgenSyntaxError):
    pass


class AgenRuntimeError(AgenError):
    retryable = True


class AgenNameError(AgenError):
    retryable = False


class AgenTypeError(AgenError):
    retryable = False


class AgenTimeoutError(AgenRuntimeError):
    pass


class StepFailedError(AgenRuntimeError):
    pass


class ServiceFailedError(AgenRuntimeError):
    retryable = False
