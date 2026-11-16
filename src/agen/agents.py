"""LLM-backed agents: ``ask(agent, prompt)``.

Providers are selected per agent with ``provider = "mock" | "openai" | "azure"``.
Credentials come from environment variables and are never stored in source:

* openai: ``OPENAI_API_KEY`` and optional ``OPENAI_BASE_URL``
* azure:  ``AZURE_OPENAI_ENDPOINT``, ``AZURE_OPENAI_API_KEY``, optional ``AZURE_OPENAI_API_VERSION``
"""

from __future__ import annotations

import json
import os
from typing import Any
from urllib.parse import quote

from . import net
from .errors import AgenRuntimeError
from .values import Agent


def ask(agent: Agent, prompt: str, *, timeout: float | None = None) -> str:
    provider = str(agent.config.get("provider", "mock")).lower()
    if provider == "mock":
        return _mock(agent, prompt)
    if provider == "openai":
        return _openai(agent, prompt, timeout)
    if provider == "azure":
        return _azure(agent, prompt, timeout)
    raise AgenRuntimeError(f"agent '{agent.name}' uses unknown provider '{provider}'", retryable=False)


def _mock(agent: Agent, prompt: str) -> str:
    words = prompt.split()
    preview = " ".join(words[:12]) + (" ..." if len(words) > 12 else "")
    return f"[{agent.name}] insight on: {preview}"


def _require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise AgenRuntimeError(f"environment variable {name} is not set", retryable=False)
    return value


def _payload(agent: Agent, prompt: str) -> dict[str, Any]:
    messages = []
    if instructions := agent.config.get("instructions"):
        messages.append({"role": "system", "content": str(instructions)})
    messages.append({"role": "user", "content": prompt})
    return {"messages": messages, "temperature": agent.config.get("temperature", 0.2)}


def _extract(text: str) -> str:
    try:
        return json.loads(text)["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise AgenRuntimeError("unexpected response from model provider") from exc


def _openai(agent: Agent, prompt: str, timeout: float | None) -> str:
    key = _require_env("OPENAI_API_KEY")
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    body = {**_payload(agent, prompt), "model": agent.config.get("model", "gpt-4o-mini")}
    text = net.request("POST", f"{base}/chat/completions", body=body,
                       headers={"Authorization": f"Bearer {key}"}, timeout=timeout)
    return _extract(text)


def _azure(agent: Agent, prompt: str, timeout: float | None) -> str:
    endpoint = _require_env("AZURE_OPENAI_ENDPOINT").rstrip("/")
    key = _require_env("AZURE_OPENAI_API_KEY")
    version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01")
    deployment = str(agent.config.get("deployment") or agent.config.get("model", "gpt-4o-mini"))
    url = f"{endpoint}/openai/deployments/{quote(deployment)}/chat/completions?api-version={quote(version)}"
    text = net.request("POST", url, body=_payload(agent, prompt), headers={"api-key": key}, timeout=timeout)
    return _extract(text)
