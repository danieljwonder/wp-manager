"""wp-manager backend routes for the Hermes Desktop page (``desktop/plugin.js``).

Mounted by the Hermes web server at ``/api/plugins/wp-manager/`` once the plugin is in
``plugins.enabled``. Reuses the plugin's ``core/`` package, loaded by file path because the host
imports this file without a package (no relative imports available here).

Routes:
    GET    /sites                    registered sites + cached status
    GET    /sites/{id}               one site: pending update items, health detail, recent actions
    POST   /sites                    add a site (connection string, or url+username+credential ref/password)
    DELETE /sites/{id}               remove a site from wp-manager
    POST   /scan                     scan every site (body: {refresh?, tag?})
    POST   /sites/{id}/scan          scan one site (body: {refresh?})
    POST   /sites/{id}/update        run updates (body: {type, items?, exclude?}); the Desktop button
                                     press is the human approval
    GET    /sites/{id}/health        live health check
    GET    /actions?site=&limit=     audit log
    GET    /connector.zip            the bundled WordPress connector as a zip (?encoding=base64 → JSON)
    POST   /connector-package        write that zip under <HERMES_HOME>/wp-manager/ and return its path
                                     (the Desktop saves it through its authenticated file download)

Handlers are plain ``def`` so FastAPI runs them in a worker thread: site requests are blocking
urllib calls and must not hold the gateway's event loop. Responses never contain secrets.
"""

from __future__ import annotations

import base64
import importlib.util
import io
import logging
import sys
import zipfile
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Response

_log = logging.getLogger(__name__)
router = APIRouter()

PLUGIN_ID = "wp-manager"
_ROOT = Path(__file__).resolve().parent.parent
_CORE_DIR = _ROOT / "core"
_CORE_MODULE = "hermes_dashboard_plugin_wp_manager_core"
_CONNECTOR_DIR = _ROOT / "connector" / "website-manager-hermes-connector"
ACTOR = "desktop"


def _load_core():
    """The plugin's ``core`` package. Reuse the agent-side copy when this process already loaded
    it (one set of locks), else load it by path under a private module name."""
    init = (_CORE_DIR / "__init__.py").resolve()
    for name, mod in list(sys.modules.items()):
        f = getattr(mod, "__file__", None)
        if f and name.endswith(".core") and Path(f).resolve() == init:
            return name
    if _CORE_MODULE not in sys.modules:
        spec = importlib.util.spec_from_file_location(_CORE_MODULE, init, submodule_search_locations=[str(_CORE_DIR)])
        if spec is None or spec.loader is None:
            raise ImportError("wp-manager core package not found")
        mod = importlib.util.module_from_spec(spec)
        sys.modules[_CORE_MODULE] = mod
        try:
            spec.loader.exec_module(mod)
        except Exception:
            sys.modules.pop(_CORE_MODULE, None)
            raise
    return _CORE_MODULE


def _core(sub: str):
    pkg = _load_core()
    full = f"{pkg}.{sub}"
    if full not in sys.modules:
        importlib.import_module(full)
    return sys.modules[full]


def _settings_getter(key: str, default: Any = None) -> Any:
    try:
        from hermes_cli.config import load_config_readonly

        cfg = load_config_readonly() or {}
        entry = ((cfg.get("plugins") or {}).get("entries") or {}).get(PLUGIN_ID) or {}
        for section in ("settings", "config"):
            values = entry.get(section)
            if isinstance(values, dict) and key in values:
                return values[key]
    except Exception:
        pass
    return default


def _setup() -> None:
    _core("settings").set_getter(_settings_getter)


def _broadcast(event: str, payload: Optional[dict] = None) -> None:
    try:
        from hermes_cli.plugin_events import broadcast_plugin_event

        broadcast_plugin_event(PLUGIN_ID, event, payload or {})
    except Exception:
        pass


def _call(fn, *args, **kwargs):
    """Run a core operation, mapping failures to scrubbed HTTP errors."""
    _setup()
    redact = _core("redact")
    registry = _core("registry")
    service = _core("service")
    credentials = _core("credentials")
    client = _core("client")
    try:
        return fn(*args, **kwargs)
    except registry.RegistryError as exc:
        msg = redact.scrub(str(exc))
        raise HTTPException(status_code=404 if msg.startswith("unknown site") else 400,
                            detail={"error": msg, "kind": "invalid"}) from None
    except (service.ServiceError, credentials.CredentialError) as exc:
        raise HTTPException(status_code=400, detail={"error": redact.scrub(str(exc)),
                                                     "kind": getattr(exc, "kind", "credential")}) from None
    except client.WPError as exc:
        raise HTTPException(status_code=502, detail={"error": redact.scrub(str(exc)), "kind": exc.kind}) from None
    except HTTPException:
        raise
    except Exception as exc:
        msg = redact.scrub(f"{type(exc).__name__}: {exc}")
        _log.warning("wp-manager API error: %s", msg)
        raise HTTPException(status_code=500, detail={"error": msg, "kind": "error"}) from None


def _scrubbed(obj: Any) -> Any:
    return _core("redact").scrub_obj(obj, full=False)


@router.get("/sites")
def get_sites():
    sites = _call(lambda: _core("service").list_sites())
    tags = sorted({t for s in sites for t in (s.get("tags") or [])})
    return _scrubbed({"sites": sites, "tags": tags})


@router.get("/sites/{site_id}")
def get_site(site_id: str):
    return _scrubbed(_call(lambda: _core("service").site_detail(site_id)))


@router.post("/sites")
def add_site(body: dict):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "expected a JSON object", "kind": "invalid"})
    allowed = {"url", "username", "label", "id", "tags", "credential", "connection_string", "app_password",
               "allow_insecure"}
    args = {k: v for k, v in body.items() if k in allowed and v not in (None, "")}
    result = _call(lambda: _core("service").add_site(args, actor=ACTOR))
    _broadcast("sites.updated", {"site": result["site"]["id"]})
    return _scrubbed(result)


@router.delete("/sites/{site_id}")
def delete_site(site_id: str):
    result = _call(lambda: _core("service").remove_site(site_id, actor=ACTOR))
    _broadcast("sites.updated", {"site": site_id})
    return _scrubbed(result)


@router.post("/scan")
def scan_all(body: Optional[dict] = None):
    body = body if isinstance(body, dict) else {}

    def run():
        sites = _core("registry").sites_for(None, body.get("tag"))
        return _core("service").scan(sites, bool(body.get("refresh")))

    result = _call(run)
    _broadcast("sites.updated", {})
    return _scrubbed(result)


@router.post("/sites/{site_id}/scan")
def scan_one(site_id: str, body: Optional[dict] = None):
    body = body if isinstance(body, dict) else {}

    def run():
        site = _core("registry").get_site(site_id)
        return _core("service").scan_site(site, bool(body.get("refresh")))

    result = _call(run)
    _broadcast("sites.updated", {"site": site_id})
    return _scrubbed(result)


@router.post("/sites/{site_id}/update")
def update_site(site_id: str, body: dict):
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail={"error": "expected a JSON object", "kind": "invalid"})
    args = {"site": site_id, "type": body.get("type"), "items": body.get("items"), "exclude": body.get("exclude")}
    result = _call(lambda: _core("service").apply_updates(args, actor=ACTOR))
    _broadcast("sites.updated", {"site": site_id})
    return _scrubbed(result)


@router.get("/sites/{site_id}/health")
def site_health(site_id: str):
    result = _call(lambda: _core("service").health_check(site_id))
    _broadcast("sites.updated", {"site": site_id})
    return _scrubbed(result)


@router.get("/actions")
def actions(site: Optional[str] = None, limit: int = Query(50, ge=1, le=500)):
    def run():
        if site:
            _core("registry").get_site(site)
        return _core("state").read_actions(site, limit)

    return _scrubbed({"actions": _call(run)})


def connector_zip_bytes() -> bytes:
    if not _CONNECTOR_DIR.is_dir():
        raise HTTPException(status_code=404, detail={"error": "the connector is not bundled with this install",
                                                     "kind": "not_found"})
    buf = io.BytesIO()
    folder = _CONNECTOR_DIR.name
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(_CONNECTOR_DIR.rglob("*")):
            rel = path.relative_to(_CONNECTOR_DIR)
            if any(part.startswith(".") or part in ("node_modules", "__pycache__") for part in rel.parts):
                continue
            if path.is_symlink() or not path.is_file():
                continue
            zf.write(path, f"{folder}/{rel.as_posix()}")
    return buf.getvalue()


@router.post("/connector-package")
def connector_package():
    """Write the connector zip under ``<HERMES_HOME>/wp-manager/`` and return its path, for the
    Desktop's authenticated file download (``captureGatewayFileDownload``)."""
    data = connector_zip_bytes()

    def write():
        path = _core("paths").data_dir() / "website-manager-hermes-connector.zip"
        path.write_bytes(data)
        return path

    path = _call(write)
    return {"path": str(path), "filename": path.name, "size": len(data)}


@router.get("/connector.zip")
def connector_zip(encoding: Optional[str] = None):
    data = connector_zip_bytes()
    name = "website-manager-hermes-connector.zip"
    if encoding == "base64":
        return {"filename": name, "size": len(data), "base64": base64.b64encode(data).decode("ascii")}
    return Response(content=data, media_type="application/zip",
                    headers={"Content-Disposition": f'attachment; filename="{name}"'})
