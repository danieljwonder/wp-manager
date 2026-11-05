"""Tolerant parsing of the connector's REST responses (namespace ``website-manager-hermes/v1``).

Shapes follow the connector (includes/class-service.php; docs/connector-api.md when present). Parsing here accepts the
documented shape and a few plausible variants so a minor connector change degrades gracefully
instead of breaking scans.
"""

from __future__ import annotations

import re
from typing import Any, Optional

UPDATE_TYPES = ("plugin", "theme", "core", "translations")
TYPE_KEYS = {"plugin": "plugins", "theme": "themes", "core": "core", "translations": "translations"}
ITEM_RE = re.compile(r"^[A-Za-z0-9_\-./:@]{1,200}$")


def _s(v: Any) -> Optional[str]:
    if v is None or v == "":
        return None
    return str(v)


def _first(d: dict, *keys: str) -> Any:
    for k in keys:
        if k in d and d[k] not in (None, ""):
            return d[k]
    return None


def _as_list(v: Any) -> list:
    if isinstance(v, list):
        return [x for x in v if isinstance(x, dict)]
    if isinstance(v, dict):
        out = []
        for key, val in v.items():
            if isinstance(val, dict):
                out.append({"_key": key, **val})
        return out
    return []


def normalize_status(raw: Any) -> dict:
    d = raw if isinstance(raw, dict) else {}
    theme = d.get("active_theme") if isinstance(d.get("active_theme"), dict) else {}
    debug = d.get("debug") if isinstance(d.get("debug"), dict) else {}
    counts = d.get("updates") if isinstance(d.get("updates"), dict) else (d.get("update_counts") or {})
    return {
        "connector_version": _s(d.get("connector_version")),
        "wp_version": _s(d.get("wp_version")),
        "php_version": _s(d.get("php_version")),
        "multisite": bool(d.get("multisite")),
        "site_name": _s(d.get("site_name")),
        "site_url": _s(d.get("site_url")),
        "home_url": _s(d.get("home_url")),
        "environment_type": _s(d.get("environment_type")),
        "wp_debug": bool(debug.get("wp_debug", d.get("wp_debug", False))),
        "wp_debug_display": bool(debug.get("wp_debug_display", False)),
        "active_theme": _s(theme.get("name")) if theme else _s(d.get("active_theme")),
        "active_theme_version": _s(theme.get("version")) if theme else None,
        "file_mods_allowed": d.get("file_mods_allowed"),
        "update_counts": normalize_counts(counts),
        "last_checked": d.get("last_checked"),
        "user_capabilities": ((d.get("user") or {}).get("capabilities") if isinstance(d.get("user"), dict) else None),
    }


def normalize_counts(c: Any) -> dict:
    c = c if isinstance(c, dict) else {}
    out = {}
    for t in ("plugins", "themes", "core", "translations"):
        try:
            out[t] = int(c.get(t) or 0)
        except (TypeError, ValueError):
            out[t] = 0
    out["total"] = sum(out.values())
    return out


def _item(t: str, d: dict) -> dict:
    if t == "plugins":
        ident = _first(d, "file", "plugin", "id", "_key")
    elif t == "themes":
        ident = _first(d, "stylesheet", "slug", "id", "_key")
    elif t == "core":
        ident = _first(d, "new_version", "version", "current") or "core"
    else:
        ident = _first(d, "item", "id", "_key") or ":".join(
            str(x) for x in (d.get("translation_type") or d.get("type"), d.get("slug"), d.get("language")) if x
        )
    return {
        "id": _s(ident),
        "name": _s(_first(d, "name", "Name", "slug")) or _s(ident),
        "slug": _s(d.get("slug")),
        "current_version": _s(_first(d, "current_version", "version_current", "from_version", "current"))
        if t != "translations" else None,
        "new_version": _s(_first(d, "new_version", "update_version", "to_version", "version")),
        "requires_wp": _s(_first(d, "requires_wp", "requires")),
        "requires_php": _s(d.get("requires_php")),
        "compatible": (d.get("compatible_wp") is not False and d.get("compatible_php") is not False),
        "auto_update": bool(_first(d, "auto_update_enabled", "auto_update", "autoupdate") or False),
        "active": d.get("active"),
        "is_major": d.get("is_major") if t == "core" else None,
        "allowed": d.get("allowed") if t == "core" else None,
        "language": _s(d.get("language")) if t == "translations" else None,
    }


def normalize_updates(raw: Any) -> dict:
    d = raw if isinstance(raw, dict) else {}
    if isinstance(d.get("updates"), dict) and not any(k in d for k in ("plugins", "themes")):
        d = {**d, **d["updates"]}
    out: dict[str, Any] = {}
    for t in ("plugins", "themes", "core", "translations"):
        items = [_item(t, x) for x in _as_list(d.get(t))]
        out[t] = [
            {k: v for k, v in i.items() if v is not None} for i in items if i.get("id")
        ]
    out["counts"] = normalize_counts({t: len(out[t]) for t in ("plugins", "themes", "core", "translations")})
    out["last_checked"] = d.get("last_checked")
    errs = d.get("refresh_errors")
    if errs:
        out["refresh_errors"] = errs if isinstance(errs, list) else [errs]
    return out


def normalize_run(raw: Any) -> dict:
    d = raw if isinstance(raw, dict) else {}
    results = []
    for r in _as_list(d.get("results")):
        results.append({
            "item": _s(_first(r, "item", "file", "stylesheet", "_key")),
            "name": _s(r.get("name")),
            "from_version": _s(r.get("from_version")),
            "to_version": _s(r.get("to_version")),
            "success": bool(r.get("success")),
            "status": _s(r.get("status")),
            "error": _s(r.get("error")),
            "active_before": r.get("active_before"),
            "active_after": r.get("active_after"),
        })
    for r in results:
        for k in [k for k, v in r.items() if v is None]:
            del r[k]
    overall = d.get("result") or ("success" if d.get("success") else ("failed" if results else "unknown"))
    all_ok = bool(results) and all(r.get("success") for r in results)
    if not results:
        all_ok = bool(d.get("success"))
    errors = d.get("errors") if isinstance(d.get("errors"), list) else []
    # Item success cannot override an operation-level failure or global upgrader errors.
    if d.get("success") is False or overall in ("failed", "partial", "unknown") or errors:
        all_ok = False
    return {
        "result": str(overall),
        "all_succeeded": all_ok,
        "results": results,
        "errors": [str(e)[:300] for e in errors][:10],
        "maintenance_off": d.get("maintenance_off"),
        "duration_ms": d.get("duration_ms"),
        "output_tail": (str(d.get("output") or "")[-1500:] or None),
    }


def normalize_health(raw: Any) -> dict:
    d = raw if isinstance(raw, dict) else {}
    db = d.get("database") if isinstance(d.get("database"), dict) else {}
    last_error = d.get("last_error") if isinstance(d.get("last_error"), dict) else None
    paused_p = d.get("paused_plugins") or []
    paused_t = d.get("paused_themes") or []
    return {
        "status": _s(d.get("status")) or ("ok" if d.get("ok") else "unknown"),
        "problems": [str(p) for p in (d.get("problems") or [])],
        "warnings": [str(w) for w in (d.get("warnings") or [])],
        "recovery_mode": bool(d.get("recovery_mode")),
        "maintenance_mode": bool(d.get("maintenance_mode")),
        "database_reachable": db.get("reachable", d.get("db_ok", True)) is not False,
        "fatal_error": bool(last_error and last_error.get("fatal")),
        "last_error": ({k: last_error.get(k) for k in ("message", "file", "line", "fatal")} if last_error else None),
        "paused_plugins": [(p.get("name") or p.get("plugin") or p.get("slug")) if isinstance(p, dict) else str(p)
                           for p in paused_p][:20],
        "paused_themes": [(p.get("name") or p.get("theme") or p.get("slug")) if isinstance(p, dict) else str(p)
                          for p in paused_t][:20],
    }
