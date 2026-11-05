"""The site registry: ``<HERMES_HOME>/wp-manager/sites.json``. Never contains a password.

Each site: ``id`` (slug), ``label``, ``url``, ``username``, ``tags``, ``credential`` (a reference:
``{"env": NAME}`` or ``{"op": "op://..."}``), ``allow_insecure``, ``created_at``.
"""

from __future__ import annotations

import ipaddress
import json
import re
import threading
import time
import urllib.parse
from typing import Any, Optional

from . import paths

SITE_ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,46}[a-z0-9])?$")
ENV_NAME_RE = re.compile(r"^WPM_[A-Z0-9_]+_APP_PASSWORD$")
OP_REF_RE = re.compile(r"^op://[^\s\x00-\x1f]{3,500}$")
TAG_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$")
USERNAME_MAX = 120

_lock = threading.RLock()


class RegistryError(ValueError):
    """Invalid input or registry state. Messages never contain secrets."""


# ------------------------------------------------------------------ validation helpers
def is_local_host(host: str) -> bool:
    host = (host or "").strip("[]").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost"):
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def normalize_url(raw: Any) -> str:
    """Canonical site URL: http(s), host, optional path; no userinfo, query or fragment."""
    if not isinstance(raw, str) or not raw.strip():
        raise RegistryError("url is required (e.g. https://example.com)")
    s = raw.strip()
    if "://" not in s:
        s = "https://" + s
    parts = urllib.parse.urlsplit(s)
    if parts.scheme.lower() not in {"http", "https"}:
        raise RegistryError("url must start with https:// (or http:// for local testing)")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise RegistryError("url must not contain a username or password; pass the username separately")
    if not parts.hostname:
        raise RegistryError("url has no host name")
    if parts.query or parts.fragment:
        raise RegistryError("url must be the site address without ?query or #fragment")
    host = parts.hostname.lower()
    if not re.match(r"^[a-z0-9.\-:\[\]]+$", host):
        raise RegistryError("url host contains unsupported characters")
    netloc = host if ":" not in host else f"[{host}]"
    try:
        port = parts.port
    except ValueError:
        raise RegistryError("url has an invalid port") from None
    if port:
        netloc += f":{port}"
    path = re.sub(r"/+$", "", parts.path or "")
    for suffix in ("/wp-admin", "/wp-login.php", "/wp-json"):
        if path.lower().endswith(suffix):
            path = path[: -len(suffix)]
    return urllib.parse.urlunsplit((parts.scheme.lower(), netloc, path, "", ""))


def transport_problem(url: str, allow_insecure: bool) -> Optional[str]:
    """Why ``url`` may not be used, or None. Plain http is refused unless opted in or local."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https":
        return None
    if parts.scheme == "http":
        if allow_insecure or is_local_host(parts.hostname or ""):
            return None
        return (
            "refusing plain http:// — the Application Password would cross the network unencrypted. "
            "Use https://, or set allow_insecure: true for this site if you accept the risk"
        )
    return "unsupported URL scheme"


def slugify(text: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-")
    s = re.sub(r"-{2,}", "-", s)[:48].strip("-")
    return s or "site"


def site_id_from_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    base = host.rsplit(".", 1)[0] if "." in host and not _is_ip(host) else host
    path = parts.path.strip("/")
    return slugify(f"{base}-{path}" if path else base)


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def check_site_id(value: Any) -> str:
    if not isinstance(value, str) or not SITE_ID_RE.match(value.strip()):
        raise RegistryError("site id must be lowercase letters, digits and hyphens (max 48 chars)")
    return value.strip()


def default_env_name(site_id: str) -> str:
    return "WPM_" + re.sub(r"[^A-Z0-9]+", "_", site_id.upper()).strip("_") + "_APP_PASSWORD"


def check_credential_ref(ref: Any) -> dict:
    """Validate ``{"env": NAME}`` or ``{"op": "op://..."}``."""
    if not isinstance(ref, dict) or len(ref) != 1:
        raise RegistryError('credential must be {"env": "WPM_<SITE>_APP_PASSWORD"} or {"op": "op://Vault/Item/field"}')
    (kind, value), = ref.items()
    if not isinstance(value, str):
        raise RegistryError("credential reference must be a string")
    value = value.strip()
    if kind == "env":
        if not ENV_NAME_RE.match(value):
            raise RegistryError(
                "credential env name must look like WPM_<SITE>_APP_PASSWORD (only WPM_*_APP_PASSWORD "
                "names are accepted, so a site can never be pointed at an unrelated secret)"
            )
        return {"env": value}
    if kind == "op":
        if not OP_REF_RE.match(value) or value.startswith("-"):
            raise RegistryError("credential op reference must look like op://Vault/Item/field")
        return {"op": value}
    raise RegistryError('credential must use "env" or "op"')


def check_tags(tags: Any) -> list[str]:
    if tags is None:
        return []
    if isinstance(tags, str):
        tags = [t for t in re.split(r"[,\s]+", tags) if t]
    if not isinstance(tags, list) or len(tags) > 20:
        raise RegistryError("tags must be a list of up to 20 short labels")
    out: list[str] = []
    for t in tags:
        if not isinstance(t, str) or not TAG_RE.match(t.strip()):
            raise RegistryError("each tag must be 1-40 letters, digits, spaces, '.', '_' or '-'")
        t = t.strip().lower()
        if t not in out:
            out.append(t)
    return out


def check_username(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RegistryError("username is required (the WordPress user that owns the Application Password)")
    v = value.strip()
    if len(v) > USERNAME_MAX or any(c in v for c in "\r\n\t:"):
        raise RegistryError("username is not valid")
    return v


def check_label(value: Any, fallback: str) -> str:
    if value is None or (isinstance(value, str) and not value.strip()):
        return fallback
    if not isinstance(value, str) or len(value.strip()) > 80 or any(ord(c) < 32 for c in value):
        raise RegistryError("label must be up to 80 printable characters")
    return value.strip()


def credential_label(ref: dict) -> str:
    if "env" in ref:
        return f"Hermes secret {ref['env']}"
    if "op" in ref:
        return f"1Password reference {ref['op']}"
    return "unknown"


# ------------------------------------------------------------------ storage
def _read() -> dict:
    path = paths.sites_file()
    if not path.exists():
        return {"version": 1, "sites": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RegistryError(f"sites.json is unreadable: {type(exc).__name__}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("sites"), list):
        raise RegistryError("sites.json has an unexpected format")
    return data


def _write(data: dict) -> None:
    paths.write_private(paths.sites_file(), json.dumps(data, indent=2, sort_keys=True) + "\n")


_SAFE_FIELDS = ("id", "label", "url", "username", "tags", "credential", "allow_insecure", "created_at")


def _clean(site: dict) -> dict:
    return {k: site[k] for k in _SAFE_FIELDS if k in site}


def list_sites() -> list[dict]:
    with _lock:
        return [_clean(s) for s in _read()["sites"] if isinstance(s, dict) and s.get("id")]


def get_site(ref: Any) -> dict:
    """Find a site by id, URL or exact label."""
    if not isinstance(ref, str) or not ref.strip():
        raise RegistryError("site is required (a site id from wp_sites)")
    ref = ref.strip()
    sites = list_sites()
    for s in sites:
        if s["id"] == ref:
            return s
    try:
        url = normalize_url(ref) if ("." in ref or "://" in ref) else None
    except RegistryError:
        url = None
    for s in sites:
        if url and s["url"].rstrip("/") == url.rstrip("/"):
            return s
    for s in sites:
        if s.get("label", "").lower() == ref.lower():
            return s
    raise RegistryError(f"unknown site {ref!r}; use wp_sites to list registered sites")


def add_site(site: dict) -> dict:
    site = _clean(site)
    site.setdefault("created_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
    with _lock:
        data = _read()
        for s in data["sites"]:
            if s.get("id") == site["id"]:
                raise RegistryError(f"a site with id {site['id']!r} already exists")
            if str(s.get("url", "")).rstrip("/") == site["url"].rstrip("/"):
                raise RegistryError(f"{site['url']} is already registered as {s.get('id')!r}")
        data["sites"].append(site)
        _write(data)
    return site


def remove_site(site_id: str) -> dict:
    with _lock:
        data = _read()
        for i, s in enumerate(data["sites"]):
            if s.get("id") == site_id:
                removed = data["sites"].pop(i)
                _write(data)
                return _clean(removed)
    raise RegistryError(f"unknown site {site_id!r}")


def sites_for(selection: Any = None, tag: Any = None) -> list[dict]:
    """Resolve a list of site ids (or all sites, optionally filtered by tag). Validates before any I/O."""
    all_sites = list_sites()
    if selection:
        if isinstance(selection, str):
            selection = [selection]
        if not isinstance(selection, list) or len(selection) > 200:
            raise RegistryError("sites must be a list of site ids")
        chosen = []
        for ref in selection:
            s = get_site(ref)
            if s not in chosen:
                chosen.append(s)
        result = chosen
    else:
        result = all_sites
    if tag:
        if not isinstance(tag, str) or not TAG_RE.match(tag.strip()):
            raise RegistryError("tag is not valid")
        t = tag.strip().lower()
        result = [s for s in result if t in (s.get("tags") or [])]
    return result
