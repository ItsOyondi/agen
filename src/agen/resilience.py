"""Retry policies: exponential backoff with jitter, attempt limits and timeouts."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from .errors import AgenError, AgenTypeError
from .values import is_int, is_number

T = TypeVar("T")


@dataclass(frozen=True)
class RetryPolicy:
    retries: int = 2
    backoff: float = 0.2
    max_backoff: float = 10.0
    timeout: float | None = None
    jitter: bool = True

    @classmethod
    def from_options(
        cls,
        options: dict[str, Any],
        *,
        retries_key: str = "retries",
        defaults: RetryPolicy | None = None,
    ) -> RetryPolicy:
        base = defaults or cls()
        allowed = {retries_key, "backoff", "max_backoff", "timeout", "jitter"}
        unknown = set(options) - allowed
        if unknown:
            raise AgenTypeError(
                f"unknown option(s) {', '.join(sorted(unknown))}; allowed: {', '.join(sorted(allowed))}"
            )

        retries = options.get(retries_key, base.retries)
        if not is_int(retries) or retries < 0:
            raise AgenTypeError(f"'{retries_key}' must be a non-negative integer")
        backoff = options.get("backoff", base.backoff)
        max_backoff = options.get("max_backoff", base.max_backoff)
        for key, value in (("backoff", backoff), ("max_backoff", max_backoff)):
            if not is_number(value) or value < 0:
                raise AgenTypeError(f"'{key}' must be a non-negative number of seconds")
        timeout = options.get("timeout", base.timeout)
        if timeout is not None and (not is_number(timeout) or timeout <= 0):
            raise AgenTypeError("'timeout' must be a positive number of seconds")
        jitter = options.get("jitter", base.jitter)
        if not isinstance(jitter, bool):
            raise AgenTypeError("'jitter' must be true or false")
        return cls(retries, float(backoff), float(max_backoff), timeout, jitter)

    def delay(self, attempt: int, rng: random.Random) -> float:
        """Backoff before the retry that follows failed attempt number ``attempt``."""
        delay = min(self.max_backoff, self.backoff * (2 ** (attempt - 1)))
        return delay * rng.uniform(0.5, 1.0) if self.jitter else delay


def call_with_retries(
    fn: Callable[[], T],
    policy: RetryPolicy,
    *,
    rng: random.Random,
    sleep: Callable[[float], None],
    on_retry: Callable[[int, AgenError, float], None],
) -> T:
    attempt = 0
    while True:
        attempt += 1
        try:
            return fn()
        except AgenError as exc:
            if not exc.retryable or attempt > policy.retries:
                raise
            delay = policy.delay(attempt, rng)
            on_retry(attempt, exc, delay)
            sleep(delay)
