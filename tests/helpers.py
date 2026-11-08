"""Test helpers: load the plugin package by path, isolate HERMES_HOME, replay recorded site responses."""

import copy
import importlib.util
import json
import shutil
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PKG_NAME = "wp_manager_under_test"

FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "site.json").read_text())
META = FIXTURE["meta"]
BASE = META["base_url"]
USER = META["username"]
# A syntactically real Application Password that must never leak anywhere.
SECRET = "AbCd EfGh IjKl MnOp QrSt UvWx"
SECRET_NOSPACE = SECRET.replace(" ", "")


def load_pkg():
    if PKG_NAME in sys.modules:
        return sys.modules[PKG_NAME]
    spec = importlib.util.spec_from_file_location(
        PKG_NAME, ROOT / "__init__.py", submodule_search_locations=[str(ROOT)]
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[PKG_NAME] = mod
    spec.loader.exec_module(mod)
    return mod


def submodule(name):
    load_pkg()
    full = f"{PKG_NAME}.{name}"
    if full not in sys.modules:
        importlib.import_module(full)
    return sys.modules[full]


def connection_string(url=BASE, user=USER, password=SECRET):
    import base64

    raw = json.dumps({"v": 1, "url": url, "user": user, "app_password": password}).encode()
    return "wpmh1:" + base64.b64encode(raw).decode()


class ReplayTransport:
    """Serves fixture responses for ``BASE``; any other host is unreachable. Records every request."""

    def __init__(self, base=BASE, variants=(), overrides=None):
        self.base = base.rstrip("/")
        self.responses = copy.deepcopy(FIXTURE["responses"])
        for v in variants:
            self.responses.update(copy.deepcopy(FIXTURE["variants"][v]))
        self.responses.update(overrides or {})
        self.calls = []

    def _key(self, method, url):
        parts = urllib.parse.urlsplit(url)
        path = parts.path or "/"
        query = urllib.parse.parse_qsl(parts.query)
        exact = f"{method} {path}" + ("?" + urllib.parse.urlencode(sorted(query)) if query else "")
        return exact, f"{method} {path}"

    def __call__(self, req):
        client = submodule("core.client")
        self.calls.append(req)
        if not req.url.startswith(self.base + "/") and req.url != self.base:
            raise client.WPError(f"could not connect to {urllib.parse.urlsplit(req.url).hostname}: refused",
                                 kind="network")
        url = req.url[len(self.base):] or "/"
        exact, loose = self._key(req.method, url)
        seq = self.responses.get(exact) or self.responses.get(loose)
        if not seq:
            body = {"code": "rest_no_route", "message": "No route was found matching the URL and request method.",
                    "data": {"status": 404}}
            return client.Response(404, {"content-type": "application/json"}, json.dumps(body).encode())
        rec = seq.pop(0) if len(seq) > 1 else seq[0]
        body = rec["body"]
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        return client.Response(rec["status"], dict(rec.get("headers") or {}), raw)

    @property
    def network_calls(self):
        return len(self.calls)

    def requests_to(self, fragment):
        return [c for c in self.calls if fragment in c.url]


class Env:
    """Isolated HERMES_HOME + fake secret store + replay transport, reset per test."""

    def __init__(self, secrets=None, transport=None, settings=None):
        self.home = Path(tempfile.mkdtemp(prefix="wpm-test-"))
        self.secrets = dict(secrets or {})
        self.transport = transport or ReplayTransport()
        self.settings = dict(settings or {})
        self.op_calls = []

    def start(self):
        submodule("core.paths").set_home_override(self.home)
        submodule("core.client").set_transport(self.transport)
        creds = submodule("core.credentials")
        creds.set_secret_getter(lambda name: self.secrets.get(name))
        creds.set_op_runner(self._op_run)
        submodule("core.settings").set_getter(lambda key, default=None: self.settings.get(key, default))
        submodule("approval").set_cron_probe(None)
        return self

    def _op_run(self, binary, ref):
        self.op_calls.append((binary, ref))
        return self.secrets.get(ref, "")

    def stop(self):
        submodule("core.paths").set_home_override(None)
        submodule("core.client").set_transport(None)
        creds = submodule("core.credentials")
        creds.set_secret_getter(None)
        creds.set_op_runner(None)
        submodule("core.settings").set_getter(None)
        submodule("core.redact").forget_all()
        shutil.rmtree(self.home, ignore_errors=True)

    def all_files_text(self):
        out = {}
        for p in self.home.rglob("*"):
            if p.is_file():
                out[str(p.relative_to(self.home))] = p.read_text(errors="replace")
        return out


def call(handler, args=None):
    return json.loads(handler(args if args is not None else {}))


def add_default_site(env, **extra):
    """Register the fixture site through the tool, with the default env secret."""
    tools = submodule("tools")
    env.secrets.setdefault("WPM_WP_EXAMPLE_APP_PASSWORD", SECRET)
    args = {"url": BASE, "username": USER, "id": "wp-example", "label": "Example", "tags": ["clients"], **extra}
    return call(tools.wp_site_add, args)
