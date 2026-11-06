"""Dashboard backend (dashboard/plugin_api.py), loaded the way the Hermes web server loads it."""

import importlib.util
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile

from helpers import BASE, ROOT, SECRET, SECRET_NOSPACE, USER, Env, ReplayTransport, connection_string, submodule

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
except ImportError:  # pragma: no cover - fastapi ships with Hermes
    FastAPI = None

API_PATH = ROOT / "dashboard" / "plugin_api.py"


def load_api():
    name = "hermes_dashboard_plugin_wp-manager"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, API_PATH)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@unittest.skipIf(FastAPI is None, "fastapi not installed")
class Api(unittest.TestCase):
    def setUp(self):
        self.env = Env().start()
        self.t = self.env.transport
        api = load_api()
        app = FastAPI()
        app.include_router(api.router, prefix="/api/plugins/wp-manager")
        self.c = TestClient(app)
        self.bodies = []

    def tearDown(self):
        self.env.stop()

    def req(self, method, path, **kw):
        r = self.c.request(method, "/api/plugins/wp-manager" + path, **kw)
        self.bodies.append(r.text)
        return r

    def add(self):
        r = self.req("POST", "/sites", json={"connection_string": connection_string(), "id": "wp-example",
                                             "tags": ["clients"], "storage": "env"})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_shares_core_with_agent_side(self):
        load_api()._load_core()
        self.assertNotIn("hermes_dashboard_plugin_wp_manager_core", sys.modules)

    def test_add_scan_update_flow(self):
        data = self.add()
        self.assertIn("WPM_WP_EXAMPLE_APP_PASSWORD", data["credential_storage"])
        sites = self.req("GET", "/sites").json()
        self.assertEqual(sites["tags"], ["clients"])
        self.assertEqual(sites["sites"][0]["id"], "wp-example")
        scan = self.req("POST", "/scan", json={"refresh": False}).json()
        self.assertEqual(scan["totals"]["total"], 5)
        one = self.req("POST", "/sites/wp-example/scan", json={}).json()
        self.assertTrue(one["ok"])
        detail = self.req("GET", "/sites/wp-example").json()
        self.assertEqual(len(detail["updates"]["plugins"]), 2)
        upd = self.req("POST", "/sites/wp-example/update",
                       json={"type": "plugin", "items": ["hello-dolly/hello.php"]}).json()
        self.assertTrue(upd["success"], upd)
        self.assertEqual(upd["health"]["verdict"], "ok")
        acts = self.req("GET", "/actions?site=wp-example").json()["actions"]
        self.assertEqual(acts[0]["action"], "update_apply")
        self.assertEqual(acts[0]["actor"], "desktop")
        health = self.req("GET", "/sites/wp-example/health").json()
        self.assertEqual(health["verdict"], "ok")
        self.assertEqual(self.req("DELETE", "/sites/wp-example").status_code, 200)
        self.assertEqual(self.req("GET", "/sites").json()["sites"], [])
        for body in self.bodies:
            self.assertNotIn(SECRET, body)
            self.assertNotIn(SECRET_NOSPACE, body)

    def test_initial_detail_hydration_uses_cached_offers_and_shared_counts(self):
        self.add()
        before = self.req("GET", "/sites/wp-example").json()
        self.assertIsNone(before["last_checked"])
        self.assertGreater(before["pending"]["total"], 0)
        self.assertFalse(any(before["updates"].values()))
        self.t.calls.clear()
        result = self.req("POST", "/sites/wp-example/scan", json={"refresh": False}).json()
        self.assertTrue(result["ok"])
        detail = self.req("GET", "/sites/wp-example").json()
        summary = self.req("GET", "/sites").json()["sites"][0]
        self.assertIsNotNone(detail["last_checked"])
        self.assertIsNone(detail["health_detail"])
        self.assertEqual(summary["pending"], detail["pending"])
        for key, items in detail["updates"].items():
            self.assertEqual(detail["pending"][key], len(items))
        self.assertEqual(detail["updates"], result["updates"])
        self.assertEqual(len(self.t.calls), 2)  # status + cached update offers only
        for call in self.t.calls:
            self.assertEqual(call.method, "GET")
            self.assertNotIn("refresh=", call.url)

    def test_add_with_password_field(self):
        r = self.req("POST", "/sites", json={"url": BASE, "username": USER, "id": "wp-example",
                                             "app_password": SECRET, "storage": "env"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn(".env", r.json()["credential_storage"])
        self.assertNotIn(SECRET_NOSPACE, r.text)

    def test_errors_are_scrubbed_json(self):
        r = self.req("POST", "/sites", json={"url": "http://plain.example.com", "username": "u"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["detail"]["kind"], "insecure")
        r = self.req("GET", "/sites/nope")
        self.assertEqual(r.status_code, 404)
        r = self.req("POST", "/sites/nope/update", json={"type": "plugin"})
        self.assertEqual(r.status_code, 404)
        self.add()
        r = self.req("POST", "/sites/wp-example/update", json={"type": "bogus"})
        self.assertEqual(r.status_code, 400)
        self.t.responses.update(ReplayTransport(variants=("locked",)).responses)
        r = self.req("POST", "/sites/wp-example/update", json={"type": "plugin"})
        self.assertEqual(r.status_code, 400)
        self.assertIn("Another update run", r.json()["detail"]["error"])

    def test_connector_zip(self):
        r = self.req("GET", "/connector.zip")
        if not (ROOT / "connector" / "website-manager-hermes-connector").is_dir():
            self.assertEqual(r.status_code, 404)
            return
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.headers["content-type"], "application/zip")
        names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
        self.assertIn("website-manager-hermes-connector/website-manager-hermes-connector.php", names)
        self.assertTrue(all(n.startswith("website-manager-hermes-connector/") for n in names))
        b = self.req("GET", "/connector.zip?encoding=base64").json()
        self.assertEqual(b["filename"], "website-manager-hermes-connector.zip")
        p = self.req("POST", "/connector-package").json()
        self.assertTrue(os.path.isfile(p["path"]))
        self.assertTrue(p["path"].startswith(str(self.env.home)))


class StandaloneImport(unittest.TestCase):
    """The host imports plugin_api.py with no package; it must find core/ by itself."""

    def test_fresh_interpreter(self):
        home = tempfile.mkdtemp(prefix="wpm-api-")
        code = (
            "import importlib.util, json, sys\n"
            f"spec = importlib.util.spec_from_file_location('hermes_dashboard_plugin_wp-manager', {str(API_PATH)!r})\n"
            "m = importlib.util.module_from_spec(spec); sys.modules[spec.name] = m; spec.loader.exec_module(m)\n"
            "print(json.dumps(m.get_sites()))\n"
        )
        self.addCleanup(shutil.rmtree, home, True)
        env = {**os.environ, "HERMES_HOME": home}
        out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, env=env, timeout=60,
                             cwd=home)
        if "No module named 'fastapi'" in out.stderr:
            self.skipTest("fastapi not installed")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertEqual(json.loads(out.stdout.strip().splitlines()[-1]), {"sites": [], "tags": []})


if __name__ == "__main__":
    unittest.main()
