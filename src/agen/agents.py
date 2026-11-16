"""LLM-backed agents: ``ask(agent, prompt)``.

Providers are selected per agent with ``provider = "mock" | "openai" | "azure"``.
Credentials come from environment variables and are never stored in source:

* openai: ``OPENAI_API_KEY`` and optional ``OPENAI_BASE_URL``.
  A ``.env`` next to the program is loaded first. If those names are absent,
  ``AZURE_OPENAI_KEY`` (or ``AZURE_OPENAI_API_KEY``) and ``AZURE_OPENAI_ENDPOINT``
  are used, which covers Azure's OpenAI-compatible ``/openai/v1`` endpoint.
* azure:  ``AZURE_OPENAI_ENDPOINT``, ``AZURE_OPENAI_API_KEY`` (or ``AZURE_OPENAI_KEY``),
  optional ``AZURE_OPENAI_API_VERSION``
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


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    joined = " or ".join(names)
    raise AgenRuntimeError(f"environment variable {joined} is not set", retryable=False)


def _payload(agent: Agent, prompt: str) -> dict[str, Any]:
    messages = []
    if instructions := agent.config.get("instructions"):
        messages.append({"role": "system", "content": str(instructions)})
    messages.append({"role": "user", "content": prompt})
    body: dict[str, Any] = {"messages": messages}
    # Omit temperature unless the program sets it. Some models only accept the default.
    if "temperature" in agent.config:
        body["temperature"] = agent.config["temperature"]
    return body


def _extract(text: str) -> str:
    try:
        return json.loads(text)["choices"][0]["message"]["content"]
    except (json.JSONDecodeError, KeyError, IndexError, TypeError) as exc:
        raise AgenRuntimeError("unexpected response from model provider") from exc


def _openai(agent: Agent, prompt: str, timeout: float | None) -> str:
    key = _first_env("OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_KEY")
    base = os.environ.get("OPENAI_BASE_URL") or os.environ.get("AZURE_OPENAI_ENDPOINT") or "https://api.openai.com/v1"
    base = base.rstrip("/")
    body = {**_payload(agent, prompt), "model": agent.config.get("model", "gpt-4o-mini")}
    headers = {"Authorization": f"Bearer {key}"}
    if "openai.azure.com" in base:
        headers["api-key"] = key
    text = net.request("POST", f"{base}/chat/completions", body=body, headers=headers, timeout=timeout)
    return _extract(text)


def _azure(agent: Agent, prompt: str, timeout: float | None) -> str:
    endpoint = _first_env("AZURE_OPENAI_ENDPOINT").rstrip("/")
    key = _first_env("AZURE_OPENAI_API_KEY", "AZURE_OPENAI_KEY")
    version = os.environ.get("AZURE_OPENAI_API_VERSION", "2024-06-01")
    deployment = str(agent.config.get("deployment") or agent.config.get("model", "gpt-4o-mini"))
    url = f"{endpoint}/openai/deployments/{quote(deployment)}/chat/completions?api-version={quote(version)}"
    text = net.request("POST", url, body=_payload(agent, prompt), headers={"api-key": key}, timeout=timeout)
    return _extract(text)
