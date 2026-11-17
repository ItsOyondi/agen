"""Agen secret store backed by Azure Key Vault.

Secret values stay in memory for the current process. They are not written to
``.env``, the journal, or the event log. Printing a secret shows its name only.

Key Vault is optional. A program contacts Azure only when it calls ``get``,
``names``, ``creds``, or ``load`` on ``KeyVaultCreds``. Other programs keep
using ``.env``. ``vault.json`` only records the vault name. Sign-in uses the
Azure CLI (``az login``). The access token is sent only to ``*.vault.azure.net``.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from . import net
from .errors import AgenRuntimeError, AgenTypeError
from .values import Builtin

API_VERSION = "7.4"
_TOKEN_TTL = 45 * 60
_NAME = re.compile(r"[A-Za-z0-9-]{1,127}")
_VAULT_NAME = re.compile(r"[A-Za-z0-9-]{3,24}")
_active: SecretStore | None = None

CLASS_SOURCE = """
class KeyVaultCreds {
    init(vault) { self.store = keyvault(vault) }
    fn get(name) { return self.store.get(name) }
    fn names() { return self.store.names() }
    fn creds() { return self.store.creds() }
    fn load() { return self.store.load() }
}
"""


class Vault:
    """A handle to one Key Vault. It holds no secret values."""

    def __init__(self, name: str, url: str, store: SecretStore) -> None:
        self.name = name
        self.url = url
        self._store = store

    def __repr__(self) -> str:
        return f'keyvault("{self.name}")'

    def __deepcopy__(self, memo: dict) -> Vault:
        return self

    def member(self, name: str) -> Any:
        if name in ("name", "url"):
            return getattr(self, name)
        if name == "get":
            return Builtin("get", lambda secret_name: self._store.get(self, secret_name))
        if name == "names":
            return Builtin("names", lambda: self._store.names(self))
        if name == "creds":
            return Builtin("creds", lambda: self._store.creds(self))
        if name == "load":
            return Builtin("load", lambda: self._store.load(self))
        raise AgenRuntimeError(f"keyvault has no attribute '{name}'", retryable=False)


class Secret:
    """A fetched secret. The value is available to the runtime, never to text output."""

    def __init__(self, vault: str, name: str, value: str) -> None:
        self.vault = vault
        self.name = name
        self._value = value

    def __repr__(self) -> str:
        return f'secret("{self.name}")'

    def __str__(self) -> str:
        return repr(self)

    def __deepcopy__(self, memo: dict) -> Secret:
        copied = Secret(self.vault, self.name, self._value)
        memo[id(self)] = copied
        return copied

    def member(self, name: str) -> Any:
        if name == "name":
            return self.name
        if name == "vault":
            return self.vault
        raise AgenRuntimeError(f"secret has no attribute '{name}'", retryable=False)


def active() -> SecretStore:
    if _active is None:
        raise AgenRuntimeError("secret store is not active", retryable=False)
    return _active


class SecretStore:
    def __init__(self, *, emit: Callable[..., None] | None = None,
                 token_provider: Callable[[], str] | None = None,
                 transport: Callable[..., str] | None = None) -> None:
        self._emit = emit or (lambda *args, **kwargs: None)
        self._token_provider = token_provider or _cli_token
        self._transport = transport or net.request
        self._cache: dict[tuple[str, str], str] = {}
        self._token = ""
        self._token_until = 0.0
        self._lock = threading.Lock()

    def install(self) -> None:
        global _active
        _active = self

    def open(self, spec: str) -> Vault:
        name, url = _vault_url(spec)
        return Vault(name, url, self)

    def get(self, vault: Vault | str, name: Any) -> Secret:
        opened = vault if isinstance(vault, Vault) else self.open(str(vault))
        secret_name = _secret_name(name)
        candidates = [secret_name]
        hyphenated = secret_name.replace("_", "-")
        if hyphenated != secret_name:
            candidates.append(hyphenated)
        last_missing = secret_name
        for candidate in candidates:
            if not _NAME.fullmatch(candidate):
                continue
            try:
                value = self._fetch(opened.url, candidate)
            except _Missing:
                last_missing = candidate
                continue
            return Secret(opened.url, candidate, value)
        raise AgenRuntimeError(
            f"secret '{last_missing}' was not found in vault '{opened.name}'", retryable=False)

    def names(self, vault: Vault | str) -> list[str]:
        opened = vault if isinstance(vault, Vault) else self.open(str(vault))
        found: list[str] = []
        url: str | None = f"{opened.url}/secrets?api-version={API_VERSION}"
        while url:
            payload = self._send("GET", url)
            values = payload.get("value", [])
            if not isinstance(values, list):
                raise AgenRuntimeError(f"unexpected secret list from vault '{opened.name}'", retryable=False)
            for item in values:
                if isinstance(item, dict) and isinstance(item.get("id"), str):
                    found.append(item["id"].rstrip("/").rsplit("/", 1)[-1])
            url = payload.get("nextLink")
            if url is not None:
                _require_same_vault(opened.url, str(url))
        return sorted(found)

    def creds(self, vault: Vault | str) -> dict[str, Secret]:
        """Every secret in the vault, as redacted handles keyed by secret name."""
        opened = vault if isinstance(vault, Vault) else self.open(str(vault))
        return {name: self.get(opened, name) for name in self.names(opened)}

    def load(self, vault: Vault | str) -> int:
        """Copy secret values into the process environment. Returns how many were loaded.

        ``AZURE-OPENAI-KEY`` is also stored as ``AZURE_OPENAI_KEY``, which is the
        name ``ask`` and ``jev`` already read. Nothing is printed or journaled.
        """
        opened = vault if isinstance(vault, Vault) else self.open(str(vault))
        loaded = self.creds(opened)
        for name, secret in loaded.items():
            os.environ[name] = secret._value
            underscored = name.replace("-", "_")
            if underscored != name:
                os.environ[underscored] = secret._value
        self._emit("secret.loaded", vault=opened.name, count=len(loaded))
        return len(loaded)

    def activate_file(self, directory: Path) -> bool:
        """Remember the vault name from ``vault.json``. Does not contact Azure or read secrets."""
        path = Path(directory) / "vault.json"
        if not path.is_file():
            return False
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return False
        vault_name = data.get("vault") if isinstance(data, dict) else None
        if not isinstance(vault_name, str) or not vault_name.strip():
            return False
        os.environ.setdefault("AGEN_KEYVAULT_NAME", vault_name.strip())
        return True

    def _fetch(self, vault_url: str, name: str) -> str:
        key = (vault_url, name)
        with self._lock:
            cached = self._cache.get(key)
        if cached is not None:
            return cached
        payload = self._send("GET", f"{vault_url}/secrets/{name}?api-version={API_VERSION}")
        value = payload.get("value")
        if not isinstance(value, str):
            raise AgenRuntimeError(f"secret '{name}' has no string value", retryable=False)
        with self._lock:
            self._cache[key] = value
        return value

    def _send(self, method: str, url: str, refresh: bool = False) -> dict[str, Any]:
        _require_same_vault(url, url)
        token = self._access_token(force=refresh)
        try:
            text = self._transport(
                method, url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=30,
            )
        except AgenRuntimeError as exc:
            if not refresh and str(exc).startswith("HTTP 401"):
                return self._send(method, url, refresh=True)
            if str(exc).startswith("HTTP 404"):
                raise _Missing from exc
            raise
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AgenRuntimeError("Key Vault returned a response that was not JSON", retryable=True) from exc
        if not isinstance(payload, dict):
            raise AgenRuntimeError("Key Vault returned an unexpected response", retryable=True)
        return payload

    def _access_token(self, force: bool = False) -> str:
        with self._lock:
            if self._token and not force and time.monotonic() < self._token_until:
                return self._token
        token = self._token_provider()
        if not isinstance(token, str) or not token.strip() or any(c.isspace() for c in token):
            raise AgenRuntimeError("Azure returned an unusable Key Vault token", retryable=False)
        with self._lock:
            self._token = token.strip()
            self._token_until = time.monotonic() + _TOKEN_TTL
            return self._token


class _Missing(Exception):
    pass


def _vault_url(spec: str) -> tuple[str, str]:
    spec = str(spec).strip()
    if spec.startswith("https://"):
        parsed = urlparse(spec)
        host = parsed.hostname or ""
        if parsed.scheme != "https" or not host.endswith(".vault.azure.net") or parsed.username or parsed.password:
            raise AgenRuntimeError(f"not an Azure Key Vault URL: {spec}", retryable=False)
        name = host.split(".", 1)[0]
        return name, f"https://{host}"
    if not _VAULT_NAME.fullmatch(spec):
        raise AgenRuntimeError(
            "vault name must be 3-24 letters, digits, or hyphens "
            "(or an https://<name>.vault.azure.net URL)",
            retryable=False,
        )
    return spec, f"https://{spec}.vault.azure.net"


def _require_same_vault(vault_url: str, url: str) -> None:
    vault_host = urlparse(vault_url).hostname
    host = urlparse(url).hostname
    if urlparse(url).scheme != "https" or host != vault_host or not str(host).endswith(".vault.azure.net"):
        raise AgenRuntimeError("refusing to send the Key Vault token to another host", retryable=False)


def _secret_name(name: Any) -> str:
    if not isinstance(name, str) or not name:
        raise AgenTypeError("secret name must be a non-empty string")
    return name


def _cli_token() -> str:
    try:
        completed = subprocess.run(
            ["az", "account", "get-access-token", "--resource", "https://vault.azure.net", "-o", "json"],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AgenRuntimeError(
            "could not run 'az account get-access-token'. Install the Azure CLI and run 'az login'.",
            retryable=False,
        ) from exc
    if completed.returncode != 0:
        detail = _redact(completed.stderr or completed.stdout)
        raise AgenRuntimeError(f"Azure CLI could not get a Key Vault token. Run 'az login'. {detail}",
                               retryable=False)
    try:
        token = json.loads(completed.stdout)["accessToken"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise AgenRuntimeError("Azure CLI returned a token response that could not be read",
                               retryable=False) from exc
    return str(token)


def _redact(text: str) -> str:
    cleaned = re.sub(r"eyJ[\w.-]{10,}", "[redacted]", text).strip()
    return cleaned[:300]
