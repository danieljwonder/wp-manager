"""Operations shared by the agent tools and the Desktop backend.

Functions validate all input before any network or credential access, raise
:class:`ServiceError` / :class:`WPError` with scrubbed messages, and record every write action
in the audit log.
"""

from __future__ import annotations

import concurrent.futures
import re
import time
import urllib.parse
from typing import Any, Optional

from . import connector, credentials, health, redact, registry, settings, state
from .client import WPClient, WPError

CONTENT_TYPES = {"post": "posts", "page": "pages"}
SAVE_STATUSES = ("draft", "pending", "publish", "private")
LIST_STATUSES = ("publish", "draft", "pending", "private", "future", "trash", "any")
SLUG_RE = re.compile(r"^[a-z0-9\u00c0-\uffff][a-z0-9\u00c0-\uffff_-]{0,199}$")
MAX_CONTENT = 2_000_000

PAGE_BUILDER_MARKERS = (
    ("Elementor", re.compile(r"data-elementor-type|class=\"[^\"]*\belementor\b|elementor-section|elementor-widget")),
    ("Divi", re.compile(r"\[et_pb_|et_pb_section")),
    ("WPBakery", re.compile(r"\[vc_row|vc_row")),
    ("Beaver Builder", re.compile(r"fl-builder-content")),
    ("Oxygen", re.compile(r"ct-section|oxy-")),
    ("Bricks", re.compile(r"brxe-")),
)


class ServiceError(ValueError):
    """Invalid input or an operation that cannot proceed. ``kind`` mirrors WPError."""

    def __init__(self, message: str, kind: str = "invalid"):
        super().__init__(redact.scrub(message))
        self.kind = kind


# ------------------------------------------------------------------ helpers
def _cfg() -> settings.Settings:
    return settings.current()


def make_client(site: dict, *, with_auth: bool = True, timeout: Optional[float] = None) -> WPClient:
    cfg = _cfg()
    client_timeout = timeout or cfg.request_timeout
    # Refuse insecure transport before touching any credential source.
    problem = registry.transport_problem(site["url"], bool(site.get("allow_insecure")))
    if problem:
        raise WPError(problem, kind="insecure")
    password = None
    if with_auth:
        try:
            password = credentials.resolve(site, cfg.op_binary)
        except credentials.CredentialError as exc:
            raise ServiceError(str(exc), kind="credential") from exc
    return WPClient(site, password, timeout=client_timeout)


def public_site(site: dict) -> dict:
    """A site record plus cached status, safe to show anywhere."""
    st = state.get(site["id"])
    status = st.get("status") or {}
    upd = st.get("updates") or {}
    counts = upd.get("counts") or status.get("update_counts") or connector.normalize_counts({})
    h = st.get("health") or {}
    ref = site.get("credential") or {}
    return {
        "id": site["id"],
        "label": site.get("label") or site["id"],
        "url": site["url"],
        "admin_url": site["url"].rstrip("/") + "/wp-admin/",
        "username": site.get("username"),
        "tags": site.get("tags") or [],
        "allow_insecure": bool(site.get("allow_insecure")),
        "insecure": site["url"].startswith("http://"),
        "credential": {"type": next(iter(ref), "env"), "where": registry.credential_label(ref) if ref else None},
        "wp_version": status.get("wp_version"),
        "php_version": status.get("php_version"),
        "connector_version": status.get("connector_version"),
        "environment_type": status.get("environment_type"),
        "active_theme": status.get("active_theme"),
        "pending": counts,
        "health": {"verdict": h.get("verdict"), "failing": h.get("failing") or [], "checked_at": h.get("checked_at")}
        if h else None,
        "last_checked": st.get("last_scan"),
        "last_error": st.get("last_error"),
    }


def site_detail(site_ref: str) -> dict:
    site = registry.get_site(site_ref)
    st = state.get(site["id"])
    out = public_site(site)
    upd = st.get("updates") or {}
    out["updates"] = {t: upd.get(t) or [] for t in ("plugins", "themes", "core", "translations")}
    out["health_detail"] = st.get("health")
    out["recent_actions"] = state.read_actions(site["id"], 20)
    return out


def list_sites() -> list[dict]:
    return [public_site(s) for s in registry.list_sites()]


# ------------------------------------------------------------------ add / remove
def plan_add(args: dict) -> dict:
    """Validate a site-add request without network access. Returns the plan (holds the password
    only in memory, under ``_password``)."""
    if not isinstance(args, dict):
        raise ServiceError("arguments must be an object")
    conn = None
    if args.get("connection_string"):
        try:
            conn = credentials.decode_connection_string(args["connection_string"])
        except credentials.CredentialError as exc:
            raise ServiceError(str(exc)) from exc
    url_in = args.get("url") or (conn or {}).get("url")
    user_in = args.get("username") or (conn or {}).get("user")
    url = registry.normalize_url(url_in)
    if conn and args.get("url") and registry.normalize_url(conn["url"]) != url:
        raise ServiceError("the url does not match the connection string's site address")
    username = registry.check_username(user_in)
    allow_insecure = bool(args.get("allow_insecure"))
    problem = registry.transport_problem(url, allow_insecure)
    if problem:
        raise ServiceError(problem, kind="insecure")
    site_id = registry.check_site_id(args["id"]) if args.get("id") else registry.site_id_from_url(url)
    label = registry.check_label(args.get("label"), urllib.parse.urlsplit(url).hostname or site_id)
    tags = registry.check_tags(args.get("tags"))
    ref = registry.check_credential_ref(args["credential"]) if args.get("credential") else None
    password = None
    store_env = False
    if ref is None:
        ref = {"env": registry.default_env_name(site_id)}
        if conn:
            password = conn["app_password"]
            store_env = True
        elif args.get("app_password"):
            password = credentials._clean_password(args["app_password"])  # noqa: SLF001
            redact.remember(password, username)
            store_env = True
    for s in registry.list_sites():
        if s["id"] == site_id:
            raise ServiceError(f"a site with id {site_id!r} already exists; pass a different id")
        if s["url"].rstrip("/") == url.rstrip("/"):
            raise ServiceError(f"{url} is already registered as {s['id']!r}")
    return {
        "site": {"id": site_id, "label": label, "url": url, "username": username, "tags": tags,
                 "credential": ref, "allow_insecure": allow_insecure},
        "store_env": store_env,
        "_password": password,
        "from_connection_string": bool(conn),
        "ignored_connection_password": bool(conn and args.get("credential")),
    }


def add_site(args: dict, *, actor: str) -> dict:
    plan = plan_add(args)
    site = plan["site"]
    cfg = _cfg()
    password = plan["_password"]
    if password is None:
        try:
            password = credentials.resolve(site, cfg.op_binary)
        except credentials.CredentialError as exc:
            raise ServiceError(str(exc), kind="credential") from exc
    client = WPClient(site, password, timeout=cfg.request_timeout)
    raw = client.connector("GET", "status")
    status = connector.normalize_status(raw)
    if not status.get("wp_version") and not status.get("connector_version"):
        raise ServiceError("the site answered, but not with a connector status; is the connector up to date?",
                           kind="bad_response")
    if plan["store_env"]:
        try:
            storage = credentials.store_in_env(site["credential"]["env"], password)
        except credentials.CredentialError as exc:
            raise ServiceError(f"connected, but the password could not be stored: {exc}", kind="credential") from exc
    else:
        storage = f"using existing {registry.credential_label(site['credential'])} (nothing was written)"
    saved = registry.add_site(site)
    state.update(site["id"], status=status, last_scan=None, last_error=None)
    state.record_action("site_add", site["id"], actor=actor, result="ok", url=site["url"],
                        credential=registry.credential_label(site["credential"]))
    out = {"site": public_site(saved), "credential_storage": storage, "verified": {
        "wp_version": status.get("wp_version"), "connector_version": status.get("connector_version"),
        "environment_type": status.get("environment_type")}}
    if plan["ignored_connection_password"]:
        out["note"] = "the connection string's password was not stored; the credential reference you gave is used"
    if site["url"].startswith("http://") and not registry.is_local_host(urllib.parse.urlsplit(site["url"]).hostname or ""):
        out["warning"] = "this site uses plain http:// (allow_insecure); the Application Password is sent unencrypted"
    return out


def remove_site(site_ref: str, *, actor: str) -> dict:
    site = registry.get_site(site_ref)
    registry.remove_site(site["id"])
    state.drop(site["id"])
    state.record_action("site_remove", site["id"], actor=actor, result="ok", url=site["url"])
    ref = site.get("credential") or {}
    return {
        "removed": site["id"],
        "note": "The Application Password was not revoked. Revoke it in WordPress (Users → Profile → Application "
        f"Passwords) and delete {registry.credential_label(ref)} if you no longer need it.",
    }


# ------------------------------------------------------------------ scan
def scan_site(site: dict, refresh: bool = False) -> dict:
    try:
        client = make_client(site)
        status = connector.normalize_status(client.connector("GET", "status"))
        updates = connector.normalize_updates(
            client.connector("GET", "updates", {"refresh": 1} if refresh else None,
                             timeout=max(_cfg().request_timeout, 120) if refresh else None))
        state.update(site["id"], status=status, updates=updates, last_scan=state.now(), last_error=None)
        return {"site": site["id"], "ok": True, "pending": updates["counts"], "wp_version": status.get("wp_version"),
                "php_version": status.get("php_version"), "connector_version": status.get("connector_version"),
                "updates": {t: updates[t] for t in ("plugins", "themes", "core", "translations")},
                **({"refresh_errors": updates["refresh_errors"]} if updates.get("refresh_errors") else {})}
    except (WPError, ServiceError) as exc:
        msg = redact.scrub(str(exc))
        state.update(site["id"], last_error={"message": msg, "kind": getattr(exc, "kind", "error"), "at": state.now()})
        return {"site": site["id"], "ok": False, "error": msg, "kind": getattr(exc, "kind", "error")}
    except Exception as exc:  # never let one site break a fleet scan
        msg = redact.scrub(f"unexpected error: {type(exc).__name__}: {exc}")
        state.update(site["id"], last_error={"message": msg, "kind": "error", "at": state.now()})
        return {"site": site["id"], "ok": False, "error": msg, "kind": "error"}


def scan(sites: list[dict], refresh: bool = False) -> dict:
    workers = max(1, min(_cfg().max_parallel, 8, len(sites) or 1))
    results: list[dict] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers, thread_name_prefix="wp-manager-scan") as pool:
        futures = {pool.submit(scan_site, s, refresh): s for s in sites}
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())
    order = {s["id"]: i for i, s in enumerate(sites)}
    results.sort(key=lambda r: order.get(r["site"], 0))
    totals = {t: 0 for t in ("plugins", "themes", "core", "translations")}
    for r in results:
        for t in totals:
            totals[t] += int((r.get("pending") or {}).get(t) or 0)
    totals["total"] = sum(totals.values())
    return {"scanned": len(results), "failed": sum(1 for r in results if not r["ok"]), "totals": totals,
            "sites": results}


# ------------------------------------------------------------------ updates
def _check_items(kind: str, items: Any, field: str) -> list[str]:
    if items is None or items == []:
        return []
    if isinstance(items, str):
        items = [items]
    if not isinstance(items, list) or len(items) > 200:
        raise ServiceError(f"{field} must be a list of item ids (or \"all\")")
    out = []
    for it in items:
        if not isinstance(it, str) or not connector.ITEM_RE.match(it.strip()) or ".." in it:
            raise ServiceError(f"{field} contains an invalid item id")
        out.append(it.strip())
    return out


def plan_update(args: dict) -> dict:
    """Validate an update request (no network). ``items``: list, "all" or empty (= all)."""
    if not isinstance(args, dict):
        raise ServiceError("arguments must be an object")
    site = registry.get_site(args.get("site"))
    utype = args.get("type")
    if utype in ("plugins", "themes", "translation"):
        utype = {"plugins": "plugin", "themes": "theme", "translation": "translations"}[utype]
    if utype not in connector.UPDATE_TYPES:
        raise ServiceError("type must be one of plugin, theme, core, translations")
    raw_items = args.get("items")
    all_items = raw_items in (None, "all", [], ["all"])
    items = [] if all_items else _check_items(utype, raw_items, "items")
    exclude = _check_items(utype, args.get("exclude"), "exclude")
    return {"site": site, "type": utype, "items": items, "all": all_items, "exclude": exclude}


def _resolve_items(plan: dict) -> list[str]:
    """Map slugs to plugin files using the cached scan, expand "all" when there are exclusions."""
    cached = (state.get(plan["site"]["id"]).get("updates") or {}).get(connector.TYPE_KEYS[plan["type"]]) or []
    if plan["type"] == "plugin":
        by_slug = {i.get("slug"): i["id"] for i in cached if i.get("slug")}
        plan["items"] = [i if i.endswith(".php") else by_slug.get(i, i) for i in plan["items"]]
        plan["exclude"] = [i if i.endswith(".php") else by_slug.get(i, i) for i in plan["exclude"]]
    if plan["all"] and plan["exclude"]:
        return [i["id"] for i in cached if i["id"] not in plan["exclude"]]
    return [i for i in plan["items"] if i not in plan["exclude"]]


def apply_updates(args: dict, *, actor: str) -> dict:
    plan = plan_update(args)
    site = plan["site"]
    cfg = _cfg()
    client = make_client(site, timeout=cfg.update_timeout)
    if plan["all"] and plan["exclude"]:
        # Fresh list so "all except X" never runs on stale data.
        upd = connector.normalize_updates(client.connector("GET", "updates"))
        state.update(site["id"], updates=upd)
    items = _resolve_items(plan)
    if not plan["all"] and not items:
        raise ServiceError("nothing to update after exclusions")
    if plan["all"] and plan["exclude"] and not items:
        raise ServiceError("nothing to update after exclusions")
    started = time.time()
    try:
        raw = client.connector("POST", "updates", body={"type": plan["type"], "items": items},
                               timeout=cfg.update_timeout)
    except WPError as exc:
        state.record_action("update_apply", site["id"], actor=actor, result="error", type=plan["type"],
                            items=items or "all", error=str(exc))
        # The site may be half-updated after a timeout or 5xx: check health anyway.
        h = _health(site)
        raise ServiceError(
            f"update request failed: {exc}. Health check after the failure: {h['verdict'].upper()}"
            + (f" — {'; '.join(h['failing'][:5])}" if h["failing"] else ""), kind=exc.kind) from exc
    run = connector.normalize_run(raw)
    h = _health(site)
    rescan = scan_site(site)
    healthy = h["verdict"] == "ok"
    success = run["all_succeeded"] and healthy
    out: dict[str, Any] = {
        "site": site["id"],
        "type": plan["type"],
        "requested": items or "all",
        "update_result": run["result"],
        "results": run["results"],
        "health": {"verdict": h["verdict"], "failing": h["failing"]},
        "success": success,
        "duration_s": round(time.time() - started, 1),
        "pending_after": rescan.get("pending"),
    }
    if run["errors"]:
        out["errors"] = run["errors"]
    if not run["all_succeeded"]:
        failed = [r.get("item") for r in run["results"] if not r.get("success")]
        out["warning"] = f"UPDATE FAILURES on {site['label']}: {', '.join(str(f) for f in failed) or run['result']}"
    if not healthy:
        out["alert"] = (
            f"HEALTH CHECK {h['verdict'].upper()} on {site['label']} ({site['url']}) after updating: "
            + "; ".join(h["failing"][:6])
            + ". Do not report this update as successful. Check the site now; restore from your host backup if needed."
        )
    state.record_action("update_apply", site["id"], actor=actor, result="ok" if success else "problem",
                        type=plan["type"], items=items or "all", update_result=run["result"],
                        health=h["verdict"], failing=h["failing"][:6],
                        changed=[{k: r.get(k) for k in ("item", "from_version", "to_version", "success")}
                                 for r in run["results"]][:50])
    return out


# ------------------------------------------------------------------ health
def _health(site: dict) -> dict:
    try:
        client = make_client(site)
    except ServiceError as exc:
        # No credential: still check the public signals.
        client = make_client(site, with_auth=False)
        res = health.check(client)
        res["failing"].append(f"connector not checked: {exc}")
        if res["verdict"] == "ok":
            res["verdict"] = "degraded"
    else:
        res = health.check(client)
    res = redact.scrub_obj(res)
    state.update(site["id"], health=res)
    return res


def health_check(site_ref: str) -> dict:
    site = registry.get_site(site_ref)
    res = _health(site)
    return {"site": site["id"], "url": site["url"], **res}


# ------------------------------------------------------------------ content
def _ctype(value: Any) -> str:
    if value not in CONTENT_TYPES:
        raise ServiceError("type must be post or page")
    return CONTENT_TYPES[value]


def _cid(value: Any) -> int:
    try:
        n = int(value)
    except (TypeError, ValueError):
        raise ServiceError("id must be a positive integer") from None
    if n <= 0 or str(value).strip().lstrip("+") != str(n):
        raise ServiceError("id must be a positive integer")
    return n


def _text(v: Any, field: str, limit: int) -> Optional[str]:
    if v is None:
        return None
    if not isinstance(v, str):
        raise ServiceError(f"{field} must be text")
    if len(v) > limit:
        raise ServiceError(f"{field} is too long")
    return v


def _raw_or_rendered(v: Any) -> Any:
    if isinstance(v, dict):
        return v.get("raw") if v.get("raw") is not None else v.get("rendered")
    return v


def detect_builder(item: dict) -> Optional[str]:
    content = item.get("content") if isinstance(item.get("content"), dict) else {}
    haystack = " ".join(str(content.get(k) or "") for k in ("raw", "rendered"))
    template = str(item.get("template") or "")
    if template.startswith("elementor_"):
        return "Elementor"
    meta = item.get("meta") if isinstance(item.get("meta"), dict) else {}
    if any(str(k).startswith("_elementor") for k in meta):
        return "Elementor"
    for name, pat in PAGE_BUILDER_MARKERS:
        if pat.search(haystack):
            return name
    return None


def _editor_kind(item: dict) -> str:
    raw = str(_raw_or_rendered(item.get("content")) or "")
    if detect_builder(item):
        return "page_builder"
    if "<!-- wp:" in raw:
        return "blocks"
    return "classic" if raw.strip() else "empty"


def _summary(item: dict) -> dict:
    return {
        "id": item.get("id"),
        "title": _raw_or_rendered(item.get("title")),
        "status": item.get("status"),
        "slug": item.get("slug"),
        "link": item.get("link"),
        "modified": item.get("modified"),
        "date": item.get("date"),
    }


def content_list(args: dict) -> dict:
    site = registry.get_site(args.get("site"))
    route = _ctype(args.get("type", "post"))
    params: dict[str, Any] = {"context": "edit", "_fields": "id,title,status,slug,link,modified,date"}
    try:
        per_page = int(args.get("per_page") or 20)
    except (TypeError, ValueError):
        raise ServiceError("per_page must be a number") from None
    params["per_page"] = max(1, min(per_page, 100))
    if args.get("search"):
        params["search"] = _text(args["search"], "search", 200)
    status = args.get("status")
    if status:
        statuses = [status] if isinstance(status, str) else status
        if not isinstance(statuses, list) or any(s not in LIST_STATUSES for s in statuses):
            raise ServiceError("status must be one of " + ", ".join(LIST_STATUSES))
        params["status"] = ",".join(statuses)
    else:
        params["status"] = "publish,draft,pending,private,future"
    client = make_client(site)
    items = client.rest("GET", f"/wp/v2/{route}", params)
    items = items if isinstance(items, list) else []
    return {"site": site["id"], "type": args.get("type", "post"), "count": len(items),
            "items": [_summary(i) for i in items if isinstance(i, dict)]}


def _fetch_item(client: WPClient, route: str, cid: int) -> dict:
    item = client.rest("GET", f"/wp/v2/{route}/{cid}", {"context": "edit"})
    if not isinstance(item, dict):
        raise ServiceError("unexpected response from the site", kind="bad_response")
    return item


def content_get(args: dict) -> dict:
    site = registry.get_site(args.get("site"))
    route = _ctype(args.get("type"))
    cid = _cid(args.get("id"))
    client = make_client(site)
    item = _fetch_item(client, route, cid)
    out = _summary(item)
    out.update({
        "site": site["id"],
        "type": args.get("type"),
        "content": _raw_or_rendered(item.get("content")),
        "excerpt": _raw_or_rendered(item.get("excerpt")),
        "editor": _editor_kind(item),
    })
    builder = detect_builder(item)
    if builder:
        out["page_builder"] = builder
        out["warning"] = (f"This {args.get('type')} is built with {builder}. Its raw content is not what renders; "
                          "editing the body here can break the layout. Edit it in wp-admin instead.")
    return out


def current_status(site: dict, ctype: str, cid: int) -> str:
    """Live status of an existing item (used by the approval hook). Raises on failure."""
    client = make_client(site, timeout=15)
    item = client.rest("GET", f"/wp/v2/{CONTENT_TYPES[ctype]}/{cid}", {"context": "edit", "_fields": "id,status"},
                       timeout=15)
    return str((item or {}).get("status") or "")


def plan_save(args: dict) -> dict:
    if not isinstance(args, dict):
        raise ServiceError("arguments must be an object")
    site = registry.get_site(args.get("site"))
    ctype = args.get("type")
    _ctype(ctype)
    cid = _cid(args["id"]) if args.get("id") not in (None, "", 0) else None
    body: dict[str, Any] = {}
    for f, limit in (("title", 1000), ("content", MAX_CONTENT), ("excerpt", 10_000)):
        v = _text(args.get(f), f, limit)
        if v is not None:
            body[f] = v
    status = args.get("status")
    if status is not None and status not in SAVE_STATUSES:
        raise ServiceError("status must be one of draft, pending, publish, private")
    if status is None and cid is None:
        status = "draft"
    if status is not None:
        body["status"] = status
    if args.get("slug") is not None:
        slug = args["slug"]
        if not isinstance(slug, str) or not SLUG_RE.match(slug):
            raise ServiceError("slug must be lowercase letters, digits and hyphens")
        body["slug"] = slug
    if cid is None and not (body.get("title") or body.get("content")):
        raise ServiceError("a new post or page needs at least a title or content")
    if cid is not None and not body:
        raise ServiceError("nothing to change: pass title, content, excerpt, status or slug")
    return {"site": site, "type": ctype, "id": cid, "body": body}


def content_save(args: dict, *, actor: str) -> dict:
    plan = plan_save(args)
    site, route, cid, body = plan["site"], CONTENT_TYPES[plan["type"]], plan["id"], plan["body"]
    client = make_client(site)
    previous = None
    if cid is not None:
        existing = _fetch_item(client, route, cid)
        previous = existing.get("status")
        builder = detect_builder(existing)
        if builder and "content" in body and not args.get("confirm_page_builder"):
            raise ServiceError(
                f"refusing to replace the body of a {builder} {plan['type']}: its raw content is not what renders, "
                "so this would likely break the layout. Ask the user to edit it in wp-admin, or pass "
                "confirm_page_builder: true if they explicitly insist", kind="page_builder")
        item = client.rest("POST", f"/wp/v2/{route}/{cid}", body=body)
    else:
        item = client.rest("POST", f"/wp/v2/{route}", body=body)
    if not isinstance(item, dict):
        raise ServiceError("unexpected response from the site", kind="bad_response")
    out = _summary(item)
    out.update({"site": site["id"], "type": plan["type"], "created": cid is None})
    if previous:
        out["previous_status"] = previous
    if item.get("status") != "publish":
        out["note"] = f"saved as {item.get('status')}; it is not public. Preview it from wp-admin before publishing."
    state.record_action("content_save", site["id"], actor=actor, result="ok", type=plan["type"],
                        id=item.get("id"), status=item.get("status"), previous_status=previous,
                        created=cid is None, title=str(_raw_or_rendered(item.get("title")) or "")[:120],
                        fields=sorted(body))
    return out
