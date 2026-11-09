"""Approval gate: a ``pre_tool_call`` hook that routes risky wp-manager calls to the human.

Gated (``{"action": "approve", "rule_key": ...}``):
  * ``wp_update_apply`` — always (rule ``wp-manager:update:<site>``);
  * ``wp_content_save`` — when it publishes, or edits an item that is already published
    (rule ``wp-manager:publish:<site>``); drafts and pending items go through;
  * ``wp_site_add`` — always: adding a site sends an existing secret (the site's default
    ``WPM_<ID>_APP_PASSWORD`` included) to a URL the model chose (rule
    ``wp-manager:credential:<site>``), or creates a new 1Password item
    (``wp-manager:credential-store:<site>``). ``allow_insecure`` adds an ``:insecure`` suffix so
    an "always allow" for the https site never covers a plain-http one.

Every other tool is exempt. Invalid arguments are not gated: the tool itself rejects them without
any network call. In unattended runs (cron, webhooks) a gated call is blocked — fail closed —
because nobody is there to approve it.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

from .core import redact, registry, service, settings

GATED_TOOLS = frozenset({"wp_update_apply", "wp_content_save", "wp_site_add"})
UNATTENDED_PLATFORMS = frozenset({"webhook", "msgraph_webhook", "api_server"})
NO_BACKUP = "wp-manager takes no backups: make sure a recent host backup exists first."

_cron_probe: Callable[[], Any] = lambda: None  # noqa: E731


def set_cron_probe(fn: Optional[Callable[[], Any]]) -> None:
    """Install ``ctx.current_cron_execution`` (returns a CronExecution or None)."""
    global _cron_probe
    _cron_probe = fn or (lambda: None)


def _truthy(v: str) -> bool:
    return str(v or "").strip().lower() in {"1", "true", "yes", "on"}


def unattended() -> Optional[str]:
    try:
        if _cron_probe() is not None:
            return "cron job"
    except Exception:
        pass
    if _truthy(os.environ.get("HERMES_CRON_SESSION", "")):
        return "cron job"
    platform = os.environ.get("HERMES_SESSION_PLATFORM", "").strip().lower()
    if platform in UNATTENDED_PLATFORMS:
        return f"{platform} session"
    return None


def _list(items: Any, limit: int = 8) -> str:
    if not items:
        return "all available"
    items = [str(i) for i in items]
    more = f" (+{len(items) - limit} more)" if len(items) > limit else ""
    return ", ".join(items[:limit]) + more


def _decide_update(args: dict) -> Optional[tuple[str, str]]:
    plan = service.plan_update(args)
    site = plan["site"]
    what = {"plugin": "plugin", "theme": "theme", "core": "WordPress core", "translations": "translation"}[plan["type"]]
    items = "all available" if plan["all"] else _list(plan["items"])
    excl = f", except {_list(plan['exclude'])}" if plan["exclude"] else ""
    msg = (f"Run {what} updates on {site['label']} ({site['url']}): {items}{excl}. "
           f"The site may briefly show a maintenance page. {NO_BACKUP}")
    return f"wp-manager:update:{site['id']}", msg


def _decide_save(args: dict) -> Optional[tuple[str, str]]:
    plan = service.plan_save(args)
    site, body, cid = plan["site"], plan["body"], plan["id"]
    noun = plan["type"]
    title = str(body.get("title") or "")[:80]
    label = f"{noun} #{cid}" if cid else f"new {noun}" + (f" “{title}”" if title else "")
    if body.get("status") == "publish":
        return (f"wp-manager:publish:{site['id']}",
                f"Publish {label} on {site['label']} ({site['url']}); it becomes publicly visible.")
    if cid is None:
        return None
    try:
        current = service.current_status(site, noun, cid)
    except Exception as exc:  # fail closed: could not prove it is unpublished
        return (f"wp-manager:publish:{site['id']}",
                f"Edit {label} on {site['label']} ({site['url']}). Its current status could not be checked "
                f"({redact.scrub(str(exc))[:120]}), so it may be live.")
    if current in ("publish", "future"):
        change = ", ".join(sorted(body))
        unpublish = " This changes its status to " + str(body["status"]) + "." if body.get("status") else ""
        return (f"wp-manager:publish:{site['id']}",
                f"Edit the already-{'published' if current == 'publish' else 'scheduled'} {label} on "
                f"{site['label']} ({site['url']}): {change}.{unpublish}")
    return None


def _decide_add(args: dict) -> Optional[tuple[str, str]]:
    """Every agent-driven site add asks first. The only secret a tool call can create is a new
    1Password item (its own prompt); any other credential already exists (the site's default
    ``WPM_<ID>_APP_PASSWORD`` included, which may be left over from a removed site), and adding
    the site sends it to the URL the model chose."""
    plan = service.plan_add(args)
    site = plan["site"]
    ref = site["credential"]
    insecure = site["url"].startswith("http://") and bool(site.get("allow_insecure"))
    warn = (" The site uses plain http:// (allow_insecure), so the Application Password crosses the "
            "network unencrypted.") if insecure else ""
    suffix = ":insecure" if insecure else ""
    if plan["storage"] == "onepassword":
        return (f"wp-manager:credential-store:{site['id']}{suffix}",
                f"Create a new 1Password item in vault {plan['vault_id']} for {site['url']} and verify "
                f"the WordPress connection.{warn}")
    return (f"wp-manager:credential:{site['id']}{suffix}",
            f"Connect {site['url']} using the existing secret {registry.credential_label(ref)}. "
            f"That secret will be sent to this site as its WordPress Application Password.{warn}")


_DECIDERS = {"wp_update_apply": _decide_update, "wp_content_save": _decide_save, "wp_site_add": _decide_add}


def decide(tool_name: str, args: Any) -> Optional[tuple[str, str]]:
    """``(rule_key, message)`` when the call needs a human, else None."""
    if tool_name not in GATED_TOOLS or not isinstance(args, dict):
        return None
    try:
        return _DECIDERS[tool_name](args)
    except (ValueError, KeyError, TypeError):
        return None  # invalid input: the handler rejects it, with zero network calls


def pre_tool_call(tool_name: str = "", args: Any = None, **kwargs: Any) -> Optional[dict]:
    if tool_name not in GATED_TOOLS:
        return None
    try:
        decision = decide(tool_name, args)
    except Exception:
        decision = (f"wp-manager:{tool_name}", f"wp-manager could not evaluate {tool_name}; approve only if expected.")
    if decision is None:
        return None
    rule_key, message = decision
    if tool_name != "wp_site_add" and not settings.current().require_approval:
        return None
    message = redact.scrub(message)
    who = unattended()
    if who:
        return {
            "action": "block",
            "message": f"wp-manager: not run — this needs human approval and this is an unattended {who}. "
                       f"({message}) Run it from an interactive chat or the Desktop WP Manager page.",
        }
    return {"action": "approve", "message": f"wp-manager: {message}", "rule_key": rule_key}
