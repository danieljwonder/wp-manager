"""Secret scrubbing for everything wp-manager writes or returns.

Two layers:

* exact redaction of every credential value this process has resolved (``remember``), plus its
  ``user:password`` Basic-auth encoding — applied to all output, including content bodies;
* pattern redaction (URL userinfo, ``Authorization`` headers, ``Basic``/``Bearer`` tokens,
  Application-Password-shaped strings, ``wpmh1:`` connection strings) — applied to error
  messages, logs, state and the audit log.
"""

from __future__ import annotations

import base64
import re
import threading
from typing import Any

MASK = "[redacted]"

_lock = threading.Lock()
_known: set[str] = set()
_MAX_KNOWN = 512

_URL_USERINFO = re.compile(r"(?i)\b([a-z][a-z0-9+.-]*://)[^\s/@'\"<>]+@")
_AUTH_HEADER = re.compile(r"(?i)\b(authorization|proxy-authorization)(\"?\s*[:=]\s*\"?)([^\r\n\"',}]+)")
_SCHEME_TOKEN = re.compile(r"(?i)\b(basic|bearer)\s+[A-Za-z0-9+/=._~-]{8,}")
# WordPress Application Passwords: 24 alphanumerics, displayed as six groups of four.
_APP_PASSWORD = re.compile(r"\b[A-Za-z0-9]{4}(?: [A-Za-z0-9]{4}){5}\b")
_CONNECTION_STRING = re.compile(r"\bwpmh1:[A-Za-z0-9+/=_-]+")
_PASSWORD_KV = re.compile(r"(?i)(\"?(?:app_password|application_password|password)\"?\s*[:=]\s*\"?)([^\s\"',}]+)")


def remember(secret: str | None, username: str | None = None) -> None:
    """Register a credential so every later output redacts it exactly."""
    if not secret or len(secret) < 4:
        return
    values = {secret, secret.replace(" ", "")}
    if username:
        for pw in list(values):
            values.add(base64.b64encode(f"{username}:{pw}".encode()).decode())
    with _lock:
        if len(_known) > _MAX_KNOWN:
            _known.clear()
        _known.update(v for v in values if len(v) >= 4)


def forget_all() -> None:
    with _lock:
        _known.clear()


def scrub_known(text: str) -> str:
    if not text:
        return text
    with _lock:
        known = sorted(_known, key=len, reverse=True)
    for value in known:
        if value in text:
            text = text.replace(value, MASK)
    return text


def scrub(text: Any) -> str:
    """Full scrub for errors, logs and the audit log."""
    s = text if isinstance(text, str) else str(text)
    s = scrub_known(s)
    s = _URL_USERINFO.sub(lambda m: m.group(1) + MASK + "@", s)
    s = _AUTH_HEADER.sub(lambda m: m.group(1) + m.group(2) + MASK, s)
    s = _SCHEME_TOKEN.sub(lambda m: m.group(1) + " " + MASK, s)
    s = _CONNECTION_STRING.sub("wpmh1:" + MASK, s)
    s = _PASSWORD_KV.sub(lambda m: m.group(1) + MASK, s)
    s = _APP_PASSWORD.sub(MASK, s)
    return s


def scrub_obj(obj: Any, full: bool = True) -> Any:
    """Recursively scrub strings in a JSON-like structure (``full=False``: exact secrets only)."""
    fn = scrub if full else scrub_known
    if isinstance(obj, str):
        return fn(obj)
    if isinstance(obj, dict):
        return {k: scrub_obj(v, full) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub_obj(v, full) for v in obj]
    return obj
