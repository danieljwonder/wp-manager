"""Tool handlers. Every handler takes ``(args, **kwargs)``, returns a JSON string and never raises.

All output passes through exact-secret redaction; errors additionally through pattern scrubbing.
"""

from __future__ import annotations

import json
import logging
from typing import Any, Callable

from .core import credentials, redact, registry, service, settings
from .core.client import WPError

log = logging.getLogger(__name__)
ACTOR = "agent"


def _ok(payload: dict) -> str:
    return json.dumps(redact.scrub_obj({"success": True, **payload}, full=False), ensure_ascii=False, default=str)


def _err(message: str, kind: str = "error", **extra: Any) -> str:
    body = {"success": False, "error": redact.scrub(message), "kind": kind, **extra}
    return json.dumps(redact.scrub_obj(body), ensure_ascii=False, default=str)


def _guard(fn: Callable[[dict], dict]) -> Callable[..., str]:
    def handler(args: Any = None, **kwargs: Any) -> str:
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return _err("arguments must be a JSON object", "invalid")
        try:
            return _ok(fn(args))
        except registry.RegistryError as exc:
            return _err(str(exc), "invalid")
        except service.ServiceError as exc:
            return _err(str(exc), exc.kind)
        except credentials.CredentialError as exc:
            return _err(str(exc), "credential")
        except WPError as exc:
            return _err(str(exc), exc.kind, **({"http_status": exc.status} if exc.status else {}))
        except Exception as exc:  # never raise into the agent loop
            log.warning("wp-manager %s failed: %s", fn.__name__, redact.scrub(f"{type(exc).__name__}: {exc}"))
            return _err(f"unexpected error: {type(exc).__name__}: {exc}", "error")

    handler.__name__ = fn.__name__
    return handler


def set_settings_getter(getter: Callable[[str, Any], Any]) -> None:
    settings.set_getter(getter)


@_guard
def wp_sites(args: dict) -> dict:
    sites = registry.sites_for(None, args.get("tag")) if args.get("tag") else registry.list_sites()
    rows = [service.public_site(s) for s in sites]
    return {
        "count": len(rows),
        "sites": rows,
        "hint": None if rows else "No sites yet. Install the connector plugin on a WordPress site, then add it "
        "from the Hermes Desktop WordPress page (paste the connection string) or with wp_site_add.",
    }


@_guard
def wp_site_add(args: dict) -> dict:
    return service.add_site(args, actor=ACTOR)


@_guard
def wp_site_remove(args: dict) -> dict:
    return service.remove_site(args.get("site"), actor=ACTOR)


@_guard
def wp_updates_scan(args: dict) -> dict:
    sites = registry.sites_for(args.get("sites"), args.get("tag"))
    if not sites:
        return {"scanned": 0, "sites": [], "note": "no matching sites"}
    return service.scan(sites, bool(args.get("refresh")))


@_guard
def wp_update_apply(args: dict) -> dict:
    return service.apply_updates(args, actor=ACTOR)


@_guard
def wp_health_check(args: dict) -> dict:
    return service.health_check(args.get("site"))


@_guard
def wp_content_list(args: dict) -> dict:
    return service.content_list(args)


@_guard
def wp_content_get(args: dict) -> dict:
    return service.content_get(args)


@_guard
def wp_content_save(args: dict) -> dict:
    return service.content_save(args, actor=ACTOR)


HANDLERS = {
    "wp_sites": wp_sites,
    "wp_site_add": wp_site_add,
    "wp_site_remove": wp_site_remove,
    "wp_updates_scan": wp_updates_scan,
    "wp_update_apply": wp_update_apply,
    "wp_health_check": wp_health_check,
    "wp_content_list": wp_content_list,
    "wp_content_get": wp_content_get,
    "wp_content_save": wp_content_save,
}
