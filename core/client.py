"""HTTP client for WordPress REST (stdlib urllib only) with a swappable transport.

Safety rules enforced here, not by callers:

* plain ``http://`` is refused unless the site opted in (``allow_insecure``) or is localhost;
* the transport never follows redirects; :class:`WPClient` follows authenticated redirects only
  within the same origin (and never https → http), so credentials cannot be carried to another host;
* every error message is scrubbed (URL userinfo, ``Authorization`` values, known secrets).
"""

from __future__ import annotations

import base64
import json
import re
import socket
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from . import CONNECTOR_NAMESPACE, VERSION, redact, registry

USER_AGENT = f"wp-manager-hermes/{VERSION}"
MAX_BYTES = 16 * 1024 * 1024
MAX_REDIRECTS = 5


class WPError(Exception):
    """A request failed. ``kind``: invalid | insecure | network | auth | forbidden | not_found |
    connector_missing | conflict | server | bad_response | redirect."""

    def __init__(self, message: str, *, status: Optional[int] = None, code: str = "", kind: str = "error"):
        super().__init__(redact.scrub(message))
        self.status = status
        self.code = code
        self.kind = kind

    def to_dict(self) -> dict:
        d: dict[str, Any] = {"error": str(self), "kind": self.kind}
        if self.status:
            d["http_status"] = self.status
        if self.code:
            d["code"] = self.code
        return d


@dataclass
class Request:
    method: str
    url: str
    headers: dict = field(default_factory=dict)
    body: Optional[bytes] = None
    timeout: float = 60.0


@dataclass
class Response:
    status: int
    headers: dict
    body: bytes


Transport = Callable[[Request], Response]


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def _urllib_transport(req: Request) -> Response:
    r = urllib.request.Request(req.url, data=req.body, method=req.method, headers=req.headers)
    try:
        with _opener.open(r, timeout=req.timeout) as resp:  # noqa: S310 - scheme validated by caller
            body = resp.read(MAX_BYTES + 1)
            if len(body) > MAX_BYTES:
                raise WPError("response too large", kind="bad_response")
            return Response(resp.status, {k.lower(): v for k, v in resp.headers.items()}, body)
    except urllib.error.HTTPError as exc:
        body = exc.read(MAX_BYTES) if exc.fp else b""
        return Response(exc.code, {k.lower(): v for k, v in (exc.headers or {}).items()}, body)
    except (ssl.SSLError, ssl.CertificateError) as exc:
        raise WPError(f"TLS error talking to {_host(req.url)}: {exc}", kind="network") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        raise WPError(f"could not connect to {_host(req.url)}: {reason}", kind="network") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise WPError(f"timed out after {int(req.timeout)}s talking to {_host(req.url)}", kind="network") from exc
    except OSError as exc:
        raise WPError(f"could not connect to {_host(req.url)}: {exc}", kind="network") from exc


_transport: Transport = _urllib_transport


def set_transport(transport: Optional[Transport]) -> None:
    """Swap the HTTP layer (tests replay recorded fixtures)."""
    global _transport
    _transport = transport or _urllib_transport


def _host(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    return parts.hostname or "?"


def _origin(url: str) -> tuple:
    p = urllib.parse.urlsplit(url)
    port = p.port or (443 if p.scheme == "https" else 80)
    return (p.scheme, (p.hostname or "").lower(), port)


def _strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _decode_json(body: bytes) -> Any:
    text = body.decode("utf-8", "replace").lstrip("\ufeff").strip()
    try:
        return json.loads(text)
    except ValueError:
        # Some hosts prepend PHP notices/BOMs before the JSON body; recover the JSON tail.
        for opener in ("{", "["):
            i = text.find(opener)
            if i > 0:
                try:
                    return json.loads(text[i:])
                except ValueError:
                    continue
        raise


def ensure_allowed(site: dict) -> None:
    problem = registry.transport_problem(site["url"], bool(site.get("allow_insecure")))
    if problem:
        raise WPError(problem, kind="insecure")


class WPClient:
    def __init__(self, site: dict, password: Optional[str] = None, timeout: float = 60.0):
        self.site = site
        self.base = site["url"].rstrip("/")
        self.timeout = float(timeout)
        self._auth = None
        if password is not None:
            token = base64.b64encode(f"{site['username']}:{password.replace(' ', '')}".encode()).decode()
            redact.remember(token)
            self._auth = "Basic " + token
        self.use_rest_route = False
        ensure_allowed(site)

    # -------------------------------------------------------------- URL building
    def _rest_url(self, route: str, params: Optional[dict]) -> str:
        route = "/" + route.lstrip("/")
        q = {k: v for k, v in (params or {}).items() if v is not None}
        if self.use_rest_route:
            return f"{self.base}/?" + urllib.parse.urlencode({"rest_route": route, **q})
        url = f"{self.base}/wp-json{route}"
        return url + ("?" + urllib.parse.urlencode(q) if q else "")

    # -------------------------------------------------------------- core send
    def _send(self, method: str, url: str, body: Optional[bytes], auth: bool, timeout: float) -> Response:
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if body is not None:
            headers["Content-Type"] = "application/json; charset=utf-8"
        if auth and self._auth:
            headers["Authorization"] = self._auth
        start_origin = _origin(url)
        for _ in range(MAX_REDIRECTS + 1):
            problem = registry.transport_problem(url, bool(self.site.get("allow_insecure")))
            if problem:
                raise WPError(problem, kind="insecure")
            resp = _transport(Request(method, url, dict(headers), body, timeout))
            if resp.status not in (301, 302, 303, 307, 308):
                return resp
            location = resp.headers.get("location", "")
            if not location:
                return resp
            target = urllib.parse.urljoin(url, location)
            same = _origin(target) == start_origin
            if auth and not same:
                raise WPError(
                    f"the site redirected to another address ({_host(target)}); credentials were not sent there. "
                    "Re-add the site using the address it redirects to",
                    status=resp.status,
                    kind="redirect",
                )
            if method != "GET" and resp.status in (301, 302, 303):
                raise WPError(
                    f"the site redirected a {method} request (HTTP {resp.status}); check the site URL",
                    status=resp.status,
                    kind="redirect",
                )
            url = target
        raise WPError("too many redirects", kind="redirect")

    def _error_from(self, resp: Response, route: str) -> WPError:
        code, message = "", ""
        try:
            data = _decode_json(resp.body)
            if isinstance(data, dict):
                code = str(data.get("code") or "")
                message = _strip_html(str(data.get("message") or ""))
        except ValueError:
            message = _strip_html(resp.body[:2000].decode("utf-8", "replace"))[:200]
        status = resp.status
        if status == 401 or code in {"rest_not_logged_in", "incorrect_password", "invalid_username", "invalid_email"}:
            return WPError(
                "WordPress rejected the credentials (check the username and that the Application Password "
                f"has not been revoked){': ' + message if message else ''}",
                status=status, code=code, kind="auth",
            )
        if status == 403:
            return WPError(f"forbidden: {message or 'the user lacks the required capability'}", status=status,
                           code=code, kind="forbidden")
        if status == 404 and code == "rest_no_route" and CONNECTOR_NAMESPACE.split("/")[0] in route:
            return WPError(
                "the Website Manager connector plugin is not installed or not active on this site "
                "(install it, then Tools → Hermes Connector)", status=status, code=code, kind="connector_missing",
            )
        if status == 404:
            return WPError(f"not found{': ' + message if message else ''}", status=status, code=code, kind="not_found")
        if status == 409:
            return WPError(message or "conflict", status=status, code=code, kind="conflict")
        if status >= 500:
            return WPError(f"site error HTTP {status}{': ' + message[:200] if message else ''}", status=status,
                           code=code, kind="server")
        return WPError(f"HTTP {status}{': ' + message[:200] if message else ''}", status=status, code=code,
                       kind="error")

    def rest(self, method: str, route: str, params: Optional[dict] = None, body: Any = None,
             auth: bool = True, timeout: Optional[float] = None) -> Any:
        if auth and not self._auth:
            raise WPError("no credential available for this site", kind="auth")
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        t = float(timeout or self.timeout)
        resp = self._send(method, self._rest_url(route, params), payload, auth, t)
        if resp.status == 404 and not self.use_rest_route and not self._looks_like_wp_json(resp):
            # Sites without pretty permalinks only answer on ?rest_route=.
            self.use_rest_route = True
            resp = self._send(method, self._rest_url(route, params), payload, auth, t)
        if resp.status >= 400:
            raise self._error_from(resp, route)
        try:
            return _decode_json(resp.body)
        except ValueError as exc:
            raise WPError(
                f"the site answered {route} with non-JSON (HTTP {resp.status}); a security plugin, cache or "
                "maintenance page may be intercepting the REST API", status=resp.status, kind="bad_response",
            ) from exc

    @staticmethod
    def _looks_like_wp_json(resp: Response) -> bool:
        try:
            data = _decode_json(resp.body)
        except ValueError:
            return False
        return isinstance(data, dict) and "code" in data

    def connector(self, method: str, path: str, params: Optional[dict] = None, body: Any = None,
                  timeout: Optional[float] = None) -> Any:
        return self.rest(method, f"/{CONNECTOR_NAMESPACE}/{path.lstrip('/')}", params, body, True, timeout)

    def fetch_page(self, url: Optional[str] = None, timeout: Optional[float] = None) -> dict:
        """Unauthenticated GET (health check). Follows redirects; returns status, timing, body text."""
        target = url or (self.base + "/")
        t = float(timeout or min(self.timeout, 30))
        started = time.monotonic()
        resp = self._send("GET", target, None, False, t)
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "status": resp.status,
            "elapsed_ms": elapsed_ms,
            "text": resp.body[:2_000_000].decode("utf-8", "replace"),
            "headers": resp.headers,
        }
