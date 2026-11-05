"""Cached per-site status (``state.json``) and the append-only audit log (``actions.jsonl``).

Both are scrubbed before writing: they never contain a credential.
"""

from __future__ import annotations

import json
import os
import threading
import time
from typing import Any, Optional

from . import paths, redact

_lock = threading.RLock()
MAX_ACTIONS_READ = 500


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _read() -> dict:
    p = paths.state_file()
    if not p.exists():
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def load_all() -> dict:
    with _lock:
        return _read()


def get(site_id: str) -> dict:
    return load_all().get(site_id) or {}


def update(site_id: str, **fields: Any) -> dict:
    """Merge ``fields`` into the site's cached state."""
    with _lock:
        data = _read()
        entry = data.get(site_id) or {}
        entry.update(redact.scrub_obj(fields))
        data[site_id] = entry
        paths.write_private(paths.state_file(), json.dumps(data, indent=2, sort_keys=True) + "\n")
        return entry


def drop(site_id: str) -> None:
    with _lock:
        data = _read()
        if site_id in data:
            del data[site_id]
            paths.write_private(paths.state_file(), json.dumps(data, indent=2, sort_keys=True) + "\n")


def record_action(action: str, site: Optional[str], *, actor: str, result: str, **details: Any) -> dict:
    entry = {"time": now(), "action": action, "site": site, "actor": actor, "result": result}
    entry.update(details)
    entry = redact.scrub_obj(entry)
    line = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    with _lock:
        path = paths.actions_file()
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    return entry


def read_actions(site: Optional[str] = None, limit: int = 50) -> list[dict]:
    limit = max(1, min(int(limit or 50), MAX_ACTIONS_READ))
    path = paths.actions_file()
    if not path.exists():
        return []
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            size = fh.tell()
            fh.seek(max(0, size - 2_000_000))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except OSError:
        return []
    out: list[dict] = []
    for line in reversed(lines):
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        if site and e.get("site") != site:
            continue
        out.append(e)
        if len(out) >= limit:
            break
    return out
