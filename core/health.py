"""Post-update (and on-demand) health checks. Verdict: ok | degraded | down.

Signals:
  home       GET the public home page: status code, response time, critical-error text
  rest       GET /wp-json/ (unauthenticated) answers JSON
  connector  GET /website-manager-hermes/v1/health (authenticated self-report)

A verdict is never "ok" unless every signal was actually observed and healthy.
"""

from __future__ import annotations

import re
import time
from typing import Any

from . import connector
from .client import WPClient, WPError, _decode_json

SLOW_MS = 10_000

CRITICAL_PATTERNS = (
    ("critical_error", re.compile(r"There has been a critical error on (?:this|your) website", re.I)),
    ("php_fatal", re.compile(r"(?:<b>)?Fatal error(?:</b>)?:\s", re.I)),
    ("db_connection_error", re.compile(r"Error establishing a database connection", re.I)),
    ("maintenance_mode", re.compile(r"Briefly unavailable for scheduled maintenance", re.I)),
    ("php_parse_error", re.compile(r"(?:<b>)?Parse error(?:</b>)?:\s", re.I)),
)


def _home(client: WPClient) -> dict:
    try:
        page = client.fetch_page()
    except WPError as exc:
        return {"ok": False, "reachable": False, "error": str(exc)}
    found = [name for name, pat in CRITICAL_PATTERNS if pat.search(page["text"])]
    status = page["status"]
    sig = {
        "reachable": True,
        "status": status,
        "response_ms": page["elapsed_ms"],
        "critical_markers": found,
    }
    sig["ok"] = 200 <= status < 400 and not found
    if page["elapsed_ms"] > SLOW_MS:
        sig["slow"] = True
    return sig


def _rest(client: WPClient) -> dict:
    try:
        url = client._rest_url("/", None)  # noqa: SLF001 - same package
        page = client.fetch_page(url)
        if page["status"] == 404 and not client.use_rest_route:
            client.use_rest_route = True
            page = client.fetch_page(client._rest_url("/", None))  # noqa: SLF001
    except WPError as exc:
        return {"ok": False, "reachable": False, "error": str(exc)}
    try:
        data = _decode_json(page["text"].encode())
        is_json = isinstance(data, dict)
    except ValueError:
        is_json = False
    return {"ok": page["status"] == 200 and is_json, "reachable": True, "status": page["status"], "json": is_json}


def _connector(client: WPClient) -> dict:
    try:
        raw = client.connector("GET", "health")
    except WPError as exc:
        return {"ok": False, "reachable": False, "error": str(exc), "kind": exc.kind}
    h = connector.normalize_health(raw)
    h["reachable"] = True
    h["ok"] = (
        h["status"] in ("ok", "warning")
        and h["database_reachable"]
        and not h["fatal_error"]
        and not h["recovery_mode"]
        and not h["paused_plugins"]
        and not h["paused_themes"]
        and not h["maintenance_mode"]
    )
    return h


def check(client: WPClient) -> dict:
    started = time.time()
    home = _home(client)
    rest = _rest(client)
    conn = _connector(client) if client._auth else {"ok": False, "reachable": False, "error": "no credential"}  # noqa: SLF001

    failing: list[str] = []
    down = False
    if not home.get("reachable"):
        failing.append(f"home page unreachable: {home.get('error')}")
        down = True
    else:
        if home.get("status", 0) >= 500:
            failing.append(f"home page returned HTTP {home['status']}")
            down = True
        elif home.get("status", 0) >= 400:
            failing.append(f"home page returned HTTP {home['status']}")
        for marker in home.get("critical_markers") or []:
            failing.append(f"home page shows {marker.replace('_', ' ')}")
            if marker in ("critical_error", "php_fatal", "db_connection_error", "php_parse_error"):
                down = True
        if home.get("slow"):
            failing.append(f"home page slow ({home['response_ms']} ms)")
    if not rest.get("ok"):
        failing.append("REST API (/wp-json/) not answering JSON" + (f": {rest['error']}" if rest.get("error") else ""))
    if not conn.get("ok"):
        if not conn.get("reachable"):
            failing.append(f"connector health unavailable: {conn.get('error')}")
        else:
            if not conn.get("database_reachable"):
                failing.append("database unreachable")
                down = True
            if conn.get("fatal_error"):
                le = conn.get("last_error") or {}
                failing.append(f"PHP fatal error: {str(le.get('message') or '')[:200]}")
            if conn.get("recovery_mode"):
                failing.append("WordPress recovery mode is active")
            if conn.get("maintenance_mode"):
                failing.append("maintenance mode still on (.maintenance file present)")
            for p in conn.get("paused_plugins") or []:
                failing.append(f"plugin paused by WordPress after a fatal error: {p}")
            for t in conn.get("paused_themes") or []:
                failing.append(f"theme paused by WordPress after a fatal error: {t}")
            if conn.get("status") not in ("ok", "warning") and not failing:
                failing.append(f"connector reports status {conn.get('status')}")

    verdict = "down" if down else ("degraded" if failing else "ok")
    return {
        "verdict": verdict,
        "failing": failing,
        "signals": {"home": home, "rest": rest, "connector": conn},
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(started)),
    }
