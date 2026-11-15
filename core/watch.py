"""Update alerts: decide what is *new* since the last check, for a Hermes no-agent cron job.

Hermes's own scheduler runs the launcher (``scripts/wp_manager_watch.py``, installed into
``<HERMES_HOME>/scripts/``) and delivers whatever it prints; empty output sends nothing. This
module only scans and compares. It never installs updates, publishes, or creates credentials.

State lives in ``<HERMES_HOME>/wp-manager/watch.json``: the watch scope, the update offers already
reported per site, and which sites were failing. An offer is identified by site, type, item and
offered version, so a newer version of the same plugin is reported again; an unchanged backlog is
not. The file is updated when the message is produced: if Hermes then fails to deliver it, Hermes
reports that delivery failure itself and this batch is not resent.
"""

from __future__ import annotations

import json
import re
import urllib.parse
from pathlib import Path
from typing import Any, Optional

from . import filelock, paths, redact, registry, service, state

LAUNCHER_NAME = "wp-manager-updates.py"
LAUNCHER_TEMPLATE = Path(__file__).resolve().parent.parent / "scripts" / "wp_manager_watch.py"
ROOT_MARKER = "__WPM_PLUGIN_ROOT__"
TYPES = ("core", "plugins", "themes", "translations")
TYPE_LABEL = {"core": "WordPress", "plugins": "Plugin", "themes": "Theme", "translations": "Translation"}
MAX_ITEM_LINES = 40
MAX_CHARS = 3500
SUGGESTED_SCHEDULE = "0 8 * * *"
JOB_NAME = "WP Manager update alerts"


# ------------------------------------------------------------------ storage
def _empty() -> dict:
    return {"scope": None, "seen": {}, "failing": {}, "initialized": False, "last_run": None,
            "last_result": None, "notices": {}}


def load() -> dict:
    p = paths.watch_file()
    if not p.exists():
        return _empty()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return _empty()
    if not isinstance(data, dict):
        return _empty()
    out = _empty()
    out.update({k: v for k, v in data.items() if k in out})
    for k in ("seen", "failing", "notices"):
        if not isinstance(out[k], dict):
            out[k] = {}
    return out


def _save(data: dict) -> None:
    paths.write_private(paths.watch_file(), json.dumps(redact.scrub_obj(data), indent=2, sort_keys=True) + "\n")


# ------------------------------------------------------------------ scope / setup
def check_scope(args: dict) -> dict:
    """Validate ``{sites?, tag?}`` against the registry (no network)."""
    sites, tag = args.get("sites"), args.get("tag")
    if sites in ([], ""):
        sites = None
    if tag in ("",):
        tag = None
    if sites is not None and tag is not None:
        raise service.ServiceError("pass either sites or tag, not both")
    chosen = registry.sites_for(sites, tag)  # raises RegistryError for unknown ids
    if not chosen:
        raise service.ServiceError("no registered sites match; add a site first" if not registry.list_sites()
                                   else f"no sites have the tag {tag!r}")
    return {"sites": [s["id"] for s in chosen] if sites is not None else None,
            "tag": registry.check_tags([tag])[0] if tag is not None else None}


def launcher_text(plugin_root: Optional[Path] = None) -> str:
    root = Path(plugin_root or LAUNCHER_TEMPLATE.parent.parent).resolve()
    text = LAUNCHER_TEMPLATE.read_text(encoding="utf-8")
    if ROOT_MARKER not in text:
        raise service.ServiceError("the update-alert launcher template is damaged; reinstall wp-manager")
    return text.replace(repr(ROOT_MARKER), repr(str(root)))


def install_launcher() -> Path:
    path = paths.scripts_dir() / LAUNCHER_NAME
    paths.write_private(path, launcher_text())
    return path


def setup(args: dict, *, actor: str) -> dict:
    scope = check_scope(args if isinstance(args, dict) else {})
    with filelock.locked(paths.watch_file()):
        data = load()
        data["scope"] = scope
        _save(data)
    path = install_launcher()
    state.record_action("watch_setup", None, actor=actor, result="ok", scope=scope, script=path.name)
    return {
        "scope": _scope_label(scope),
        "script": path.name,
        "installed_at": str(path),
        "next_step": {
            "tool": "cronjob",
            "arguments": {"action": "create", "name": JOB_NAME, "schedule": SUGGESTED_SCHEDULE,
                          "script": path.name, "no_agent": True, "deliver": "<where the user wants alerts>"},
        },
        "note": ("Now schedule it with the cronjob tool using these arguments. Ask the user how often and where to "
                 "send alerts (one of the messaging platforms they have connected to Hermes, or platform:chat_id); "
                 "keep no_agent true so "
                 "no model runs. First check any existing cronjob list entry named "
                 f"{JOB_NAME!r} and update it instead of creating a duplicate. Running setup again only changes "
                 "which sites are watched. The first run sends one summary of what is already pending; after "
                 "that, only new updates. Nothing is ever installed automatically."),
    }


def _scope_label(scope: Optional[dict]) -> str:
    if not scope:
        return "not set up"
    if scope.get("sites"):
        return "sites: " + ", ".join(scope["sites"])
    if scope.get("tag"):
        return f"sites tagged {scope['tag']!r}"
    return "all sites"


def scope_sites(scope: Optional[dict]) -> list[dict]:
    """Registered sites in scope. Sites removed since setup are skipped, not an error."""
    scope = scope or {}
    if scope.get("sites"):
        known = {s["id"]: s for s in registry.list_sites()}
        return [known[i] for i in scope["sites"] if i in known]
    try:
        return registry.sites_for(None, scope.get("tag"))
    except registry.RegistryError:
        return []


def status() -> dict:
    data = load()
    launcher = paths.hermes_home() / "scripts" / LAUNCHER_NAME
    sites = scope_sites(data["scope"]) if data["scope"] else []
    return {
        "set_up": bool(data["scope"]) and launcher.is_file(),
        "scope": _scope_label(data["scope"]),
        "sites": [s["id"] for s in sites],
        "script": LAUNCHER_NAME if launcher.is_file() else None,
        "last_run": data["last_run"],
        "last_result": data["last_result"],
        "failing": data["failing"],
        "reported_offers": sum(len(v or []) for v in data["seen"].values()),
        "schedule_note": f"Scheduling and delivery are Hermes cron jobs (look for {JOB_NAME!r} in cronjob list).",
    }


# ------------------------------------------------------------------ comparison
def offer_key(utype: str, item: dict) -> str:
    return f"{utype}:{item.get('id')}@{item.get('new_version') or ''}"


def offers(updates: dict) -> dict[str, tuple[str, dict]]:
    out: dict[str, tuple[str, dict]] = {}
    for t in TYPES:
        for it in (updates or {}).get(t) or []:
            if isinstance(it, dict) and it.get("id"):
                out[offer_key(t, it)] = (t, it)
    return out


_UNSAFE = re.compile(r"[\x00-\x1f\x7f<>`*_\[\]|]")


def clean(value: Any, limit: int = 80) -> str:
    """Site- and plugin-supplied text is untrusted: strip control/markup characters, bound length."""
    s = _UNSAFE.sub(" ", str(value or ""))
    s = " ".join(s.split())
    return s if len(s) <= limit else s[: limit - 1] + "…"


def _host(url: str) -> str:
    return clean(urllib.parse.urlsplit(url).hostname or url, 60)


def _item_line(utype: str, it: dict) -> str:
    name = clean(it.get("name") or it.get("slug") or it.get("id"))
    if utype == "core":
        name = "WordPress core"
    elif utype == "translations" and it.get("language"):
        name = f"{name} ({clean(it['language'], 20)})"
    cur, new = clean(it.get("current_version"), 30), clean(it.get("new_version"), 30)
    ver = f" {cur} → {new}" if cur and new else (f" → {new}" if new else "")
    flags = []
    if utype == "core" and it.get("is_major"):
        flags.append("major")
    if it.get("compatible") is False:
        flags.append("may be incompatible")
    return (f"- {name}{ver}" if utype == "core" else f"- {TYPE_LABEL[utype]}: {name}{ver}") + \
        (f" ({', '.join(flags)})" if flags else "")


def run() -> str:
    """One scheduled check. Returns the message to deliver, or "" when there is nothing new."""
    scope = load()["scope"]
    if not scope:
        return ""
    sites = scope_sites(scope)
    # Network outside the lock: a slow site must not block setup/status in other processes.
    result = service.scan(sites, refresh=True) if sites else {"sites": []}
    with filelock.locked(paths.watch_file()):
        data = load()
        known_ids = {s["id"] for s in registry.list_sites()}
        for k in ("seen", "failing"):
            data[k] = {sid: v for sid, v in data[k].items() if sid in known_ids}
        first = not data["initialized"]
        by_id = {s["id"]: s for s in sites}
        new_items: list[tuple[dict, list[str]]] = []
        failures: list[str] = []
        recovered: list[str] = []
        partial: list[str] = []
        total_new = 0
        for r in result["sites"]:
            sid = r["site"]
            site = by_id.get(sid) or {"id": sid, "label": sid, "url": sid}
            label = f"{clean(site.get('label') or sid, 60)} ({_host(site['url'])})"
            if not r.get("ok"):
                kind = str(r.get("kind") or "error")
                if data["failing"].get(sid) != kind:
                    failures.append(f"- {label}: {clean(redact.scrub(str(r.get('error') or kind)), 160)}")
                data["failing"][sid] = kind
                continue
            if sid in data["failing"]:
                del data["failing"][sid]
                if not first:
                    recovered.append(f"- {label}")
            current = offers(r.get("updates") or {})
            before = set(data["seen"].get(sid) or [])
            fresh = [current[k] for k in sorted(current) if k not in before]
            complete = not r.get("refresh_errors")
            data["seen"][sid] = sorted(current) if complete else sorted(before | set(current))
            if not complete:
                partial.append(label)
            if fresh:
                fresh.sort(key=lambda p: (TYPES.index(p[0]), str(p[1].get("name") or p[1].get("id")).lower()))
                new_items.append((site, [_item_line(t, it) for t, it in fresh]))
                total_new += len(fresh)
        data["initialized"] = True
        data["last_run"] = state.now()
        data["last_result"] = {"sites": len(result["sites"]), "failed": sum(1 for r in result["sites"] if not r.get("ok")),
                               "new": total_new, "first_run": first}
        _save(data)
    return render(new_items, total_new, failures, recovered, partial, first)


def render(new_items, total_new, failures, recovered, partial, first) -> str:
    if not (new_items or failures or recovered):
        return ""
    lines: list[str] = []
    n_sites = len(new_items)
    if total_new:
        what = "pending" if first else "new"
        lines.append(f"WP Manager: {total_new} {what} update{'s' if total_new != 1 else ''} on "
                     f"{n_sites} site{'s' if n_sites != 1 else ''}")
    else:
        lines.append("WP Manager update check")
    shown = 0
    for site, items in new_items:
        if shown >= MAX_ITEM_LINES:
            break
        lines.append("")
        lines.append(f"{clean(site.get('label') or site['id'], 60)} ({_host(site['url'])})")
        for line in items:
            if shown >= MAX_ITEM_LINES:
                break
            lines.append(line)
            shown += 1
    if total_new > shown:
        lines.append(f"…and {total_new - shown} more. Open WP Manager or ask Hermes for the full list.")
    if failures:
        lines += ["", "Could not check:"] + failures
    if recovered:
        lines += ["", "Working again:"] + recovered
    if partial and total_new:
        lines += ["", "WordPress.org could not be fully re-checked for: " + ", ".join(partial[:10])
                  + ". Listed updates are what the site reported."]
    lines += ["", "Nothing was installed."]
    text = "\n".join(lines)
    if len(text) > MAX_CHARS:
        text = text[: MAX_CHARS - 40].rsplit("\n", 1)[0] + "\n…(truncated)\n\nNothing was installed."
    return redact.scrub(text)


def disabled_notice() -> str:
    """Printed once when the plugin is no longer enabled, so the cron job's owner can remove it."""
    with filelock.locked(paths.watch_file()):
        data = load()
        if data["notices"].get("disabled"):
            return ""
        data["notices"]["disabled"] = state.now()
        _save(data)
    return (f"WP Manager update alerts: the wp-manager plugin is not enabled, so no sites were checked. "
            f"Remove or pause the {JOB_NAME!r} cron job, or enable the plugin again.")


def clear_disabled_notice() -> None:
    with filelock.locked(paths.watch_file()):
        data = load()
        if data["notices"].pop("disabled", None):
            _save(data)
