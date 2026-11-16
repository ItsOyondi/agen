"""Minimal HTTP client (stdlib only) with fault classification."""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from .errors import AgenRuntimeError

DEFAULT_TIMEOUT = 30.0


def request(
    method: str,
    url: str,
    *,
    body: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> str:
    if urlparse(url).scheme not in ("http", "https"):
        raise AgenRuntimeError(f"only http(s) URLs are allowed: {url}", retryable=False)

    all_headers = dict(headers or {})
    data = None
    if body is not None:
        if isinstance(body, (dict, list)):
            data = json.dumps(body).encode("utf-8")
            all_headers.setdefault("Content-Type", "application/json")
        else:
            data = str(body).encode("utf-8")

    req = urllib.request.Request(url, data=data, headers=all_headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout or DEFAULT_TIMEOUT) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        # Throttling and server errors are transient; other 4xx responses won't fix themselves.
        retryable = exc.code == 429 or exc.code >= 500
        raise AgenRuntimeError(f"HTTP {exc.code} from {url}", retryable=retryable) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise AgenRuntimeError(f"request to {url} failed: {exc}") from exc
