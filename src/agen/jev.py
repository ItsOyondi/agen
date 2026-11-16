"""JEV typed questions through TypeSafe's System One API.

One request asks several typed questions about the same input and returns
probabilities, so a program can branch on confidence instead of parsing text:

* ``choice``  picks one label from a map of labels to descriptions.
* ``score``   rates against an ordered rubric (index 0 is the lowest level).
* ``noul``    returns the probability that a statement is true.

Environment: ``TYPESAFE_API_KEY`` (required), ``TYPESAFE_BASE_URL`` and
``TYPESAFE_DEFAULT_MODEL`` (optional). See https://docs.typesafe.ai/.
"""

from __future__ import annotations

import json
import os
from typing import Any

from . import net
from .errors import AgenRuntimeError, AgenTypeError

DEFAULT_BASE_URL = "https://api.typesafe.ai"
DEFAULT_MODEL = "jev-latest"
SYSTEM_ONE_PATH = "/v1/systemone"
KINDS = ("choice", "score", "noul")


def question(kind: str, instructions: Any, criteria: Any) -> dict[str, Any]:
    q: dict[str, Any] = {"type": kind}
    if instructions is not None:
        q["instructions"] = instructions
    if criteria is not None:
        q["criteria"] = criteria
    _validate(kind, q)
    return q


def validate_questions(questions: Any) -> dict[str, Any]:
    if not isinstance(questions, dict) or not questions:
        raise AgenTypeError("jev() expects a non-empty map of questions")
    for name, q in questions.items():
        if not isinstance(q, dict) or q.get("type") not in KINDS:
            raise AgenTypeError(f"question '{name}' must be built with choice(), score() or noul()")
        _validate(name, q)
    return questions


def _validate(name: str, q: dict[str, Any]) -> None:
    kind, criteria = q["type"], q.get("criteria")
    if kind == "choice" and (not isinstance(criteria, dict) or not criteria):
        raise AgenTypeError(f"choice question '{name}' needs a non-empty map of labels")
    if kind == "score" and (not isinstance(criteria, list) or not criteria):
        raise AgenTypeError(f"score question '{name}' needs a non-empty list of levels")
    if kind == "noul" and criteria is not None and (
            not isinstance(criteria, dict) or not set(criteria) <= {"true", "false"}):
        raise AgenTypeError(f"noul question '{name}' criteria may only have 'true' and 'false'")


def system_one(state: Any, questions: dict[str, Any], *, model: str | None = None,
               timeout: float | None = None) -> dict[str, Any]:
    """Ask all questions about ``state`` in one request and return answers keyed by name."""
    api_key = os.environ.get("TYPESAFE_API_KEY", "").strip()
    if not api_key:
        raise AgenRuntimeError("environment variable TYPESAFE_API_KEY is not set", retryable=False)
    base = (os.environ.get("TYPESAFE_BASE_URL", "").strip() or DEFAULT_BASE_URL).rstrip("/")
    body = {
        "state": state,
        "model": model or os.environ.get("TYPESAFE_DEFAULT_MODEL", "").strip() or DEFAULT_MODEL,
        "questions": questions,
    }
    text = net.request(
        "POST", base + SYSTEM_ONE_PATH, body=body, timeout=timeout,
        headers={"Authorization": f"Bearer {api_key}", "Accept": "application/json"},
    )
    try:
        answers = json.loads(text)["answers"]
    except (ValueError, KeyError, TypeError) as exc:
        raise AgenRuntimeError(f"unexpected response from TypeSafe: {exc}", retryable=False) from exc
    return {name: answer for name, answer in answers.items()
            if isinstance(answer, dict) and answer.get("type") in KINDS}
