"""Minimal HTTP client (stdlib only) with connection reuse and fault classification.

Keep-alive connections go back to a shared pool after each response, so repeated
calls to the same API (including the next parallel batch) skip the TCP and TLS
handshakes. When a proxy is configured the request goes through urllib instead.
"""

from __future__ import annotations

import atexit
import http.client
import json
import ssl
import threading
import urllib.error
import urllib.request
from typing import Any, Optional
from urllib.parse import ParseResult, urljoin, urlparse

from .errors import AgenRuntimeError

DEFAULT_TIMEOUT = 30.0
_MAX_REDIRECTS = 5
_REDIRECTS = frozenset({301, 302, 303, 307, 308})
# The server closed an idle pooled connection before reading the request.
_STALE = (
    http.client.RemoteDisconnected,
    http.client.CannotSendRequest,
    http.client.BadStatusLine,
    ConnectionResetError,
    ConnectionAbortedError,
    BrokenPipeError,
)

_MAX_IDLE_PER_HOST = 16

_Key = tuple[str, str, Optional[int]]
_ssl_context = ssl.create_default_context()
_idle: dict[_Key, list[http.client.HTTPConnection]] = {}
_pool_lock = threading.Lock()
_proxies: dict[str, str] | None = None


def request(
    method: str,
    url: str,
    *,
    body: Any = None,
    headers: dict[str, str] | None = None,
    timeout: float | None = None,
) -> str:
    data, all_headers = _encode(body, headers)
    timeout = timeout or DEFAULT_TIMEOUT
    for _ in range(_MAX_REDIRECTS + 1):
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            raise AgenRuntimeError(f"only http(s) URLs are allowed: {_safe(parsed)}", retryable=False)
        if _uses_proxy(parsed):
            return _via_urllib(method, url, data, all_headers, timeout)
        status, location, payload = _send(parsed, method, data, all_headers, timeout)
        if status in _REDIRECTS and location:
            url = urljoin(url, location)
            if status == 303 or (status in (301, 302) and method not in ("GET", "HEAD")):
                method, data = "GET", None
                all_headers.pop("Content-Type", None)
            continue
        if status >= 400:
            raise _http_error(status, parsed, payload)
        return payload.decode("utf-8", errors="replace")
    raise AgenRuntimeError(f"too many redirects from {_safe(urlparse(url))}", retryable=False)


def _encode(body: Any, headers: dict[str, str] | None) -> tuple[bytes | None, dict[str, str]]:
    all_headers = {"User-Agent": "agen", **(headers or {})}
    if body is None:
        return None, all_headers
    if isinstance(body, (dict, list)):
        all_headers.setdefault("Content-Type", "application/json")
        return json.dumps(body).encode("utf-8"), all_headers
    return str(body).encode("utf-8"), all_headers


def _send(parsed: ParseResult, method: str, data: bytes | None, headers: dict[str, str],
          timeout: float) -> tuple[int, str | None, bytes]:
    key = (parsed.scheme, parsed.hostname or "", parsed.port)
    path = (parsed.path or "/") + (f"?{parsed.query}" if parsed.query else "")
    for attempt in range(2):
        conn, reused = _checkout(key, timeout)
        try:
            conn.request(method, path, body=data, headers=headers)
            response = conn.getresponse()
            payload = response.read()
        except _STALE as exc:
            conn.close()
            if reused and attempt == 0:
                continue
            raise AgenRuntimeError(f"request to {_safe(parsed)} failed: {exc}") from exc
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise AgenRuntimeError(f"request to {_safe(parsed)} failed: {exc}") from exc
        if response.will_close:
            conn.close()
        else:
            _checkin(key, conn)
        return response.status, response.getheader("Location"), payload
    raise AssertionError("unreachable")


def _checkout(key: _Key, timeout: float) -> tuple[http.client.HTTPConnection, bool]:
    with _pool_lock:
        idle = _idle.get(key)
        conn = idle.pop() if idle else None
    if conn is not None:
        conn.timeout = timeout
        if conn.sock is not None:
            conn.sock.settimeout(timeout)
        return conn, True
    scheme, host, port = key
    if scheme == "https":
        return http.client.HTTPSConnection(host, port, timeout=timeout, context=_ssl_context), False
    return http.client.HTTPConnection(host, port, timeout=timeout), False


def _checkin(key: _Key, conn: http.client.HTTPConnection) -> None:
    with _pool_lock:
        idle = _idle.setdefault(key, [])
        if len(idle) < _MAX_IDLE_PER_HOST:
            idle.append(conn)
            return
    conn.close()


def close_connections() -> None:
    with _pool_lock:
        conns = [conn for idle in _idle.values() for conn in idle]
        _idle.clear()
    for conn in conns:
        conn.close()


atexit.register(close_connections)


def _uses_proxy(parsed: ParseResult) -> bool:
    global _proxies
    if _proxies is None:
        _proxies = urllib.request.getproxies()
    if not _proxies.get(parsed.scheme):
        return False
    return not urllib.request.proxy_bypass(parsed.hostname or "")


def _via_urllib(method: str, url: str, data: bytes | None, headers: dict[str, str], timeout: float) -> str:
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    parsed = urlparse(url)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raise _http_error(exc.code, parsed, exc.read()) from exc
    except (urllib.error.URLError, OSError) as exc:
        raise AgenRuntimeError(f"request to {_safe(parsed)} failed: {exc}") from exc


def _http_error(status: int, parsed: ParseResult, payload: bytes) -> AgenRuntimeError:
    # Throttling and server errors are transient; other 4xx responses won't fix themselves.
    retryable = status == 429 or status >= 500
    detail = payload[:200].decode("utf-8", errors="replace").strip()
    message = f"HTTP {status} from {_safe(parsed)}" + (f": {detail}" if detail else "")
    return AgenRuntimeError(message, retryable=retryable)


def _safe(parsed: ParseResult) -> str:
    """URL without credentials or query string, which can carry tokens (SAS, signed URLs)."""
    port = f":{parsed.port}" if parsed.port else ""
    return f"{parsed.scheme}://{parsed.hostname or ''}{port}{parsed.path}"
