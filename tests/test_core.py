import json
import os
import subprocess
import unittest
from pathlib import Path
from unittest import mock

from helpers import BASE, SECRET, USER, Env, ReplayTransport, connection_string, submodule

registry = submodule("core.registry")
credentials = submodule("core.credentials")
connector = submodule("core.connector")
client_mod = submodule("core.client")
settings = submodule("core.settings")


class Urls(unittest.TestCase):
    def test_normalize(self):
        n = registry.normalize_url
        self.assertEqual(n("Example.COM"), "https://example.com")
        self.assertEqual(n("https://example.com/blog/"), "https://example.com/blog")
        self.assertEqual(n("https://example.com/wp-admin/"), "https://example.com")
        self.assertEqual(n("https://example.com:8443"), "https://example.com:8443")
        for bad in ("", "ftp://x.com", "javascript:alert(1)", "https://u:p@x.com", "https://x.com/?a=1",
                    "https://x.com/#f", None, 5):
            with self.assertRaises(registry.RegistryError, msg=bad):
                n(bad)

    def test_transport_rules(self):
        self.assertIsNone(registry.transport_problem("https://x.com", False))
        self.assertIsNotNone(registry.transport_problem("http://x.com", False))
        self.assertIsNone(registry.transport_problem("http://x.com", True))
        for local in ("http://localhost:8080", "http://127.0.0.1", "http://[::1]:9400", "http://wp.localhost"):
            self.assertIsNone(registry.transport_problem(local, False), local)

    def test_ids_and_env_names(self):
        self.assertEqual(registry.site_id_from_url("https://www.My-Shop.co.nz/blog"), "my-shop-co-blog")
        self.assertEqual(registry.default_env_name("my-shop"), "WPM_MY_SHOP_APP_PASSWORD")
        with self.assertRaises(registry.RegistryError):
            registry.check_site_id("Bad_ID")
        with self.assertRaises(registry.RegistryError):
            registry.check_credential_ref({"env": "AWS_SECRET_ACCESS_KEY"})
        with self.assertRaises(registry.RegistryError):
            registry.check_credential_ref({"op": "--help"})
        with self.assertRaises(registry.RegistryError):
            registry.check_credential_ref({"env": "WPM_X_APP_PASSWORD", "op": "op://a/b/c"})
        self.assertEqual(registry.check_credential_ref({"op": "op://Private/Site/password"}),
                         {"op": "op://Private/Site/password"})

    def test_tags(self):
        self.assertEqual(registry.check_tags("Clients, shop"), ["clients", "shop"])
        with self.assertRaises(registry.RegistryError):
            registry.check_tags(["<script>"])


class Storage(unittest.TestCase):
    def setUp(self):
        self.env = Env().start()

    def tearDown(self):
        self.env.stop()

    def test_sites_json_private_and_secret_free(self):
        registry.add_site({"id": "a", "label": "A", "url": "https://a.test", "username": "u", "tags": [],
                           "credential": {"env": "WPM_A_APP_PASSWORD"}, "password": SECRET})
        path = self.env.home / "wp-manager" / "sites.json"
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertNotIn(SECRET, path.read_text())
        self.assertNotIn("password", json.loads(path.read_text())["sites"][0])

    def test_no_env_file_writer(self):
        """Catalog rule 11: credentials come through Hermes; the plugin has no .env writer."""
        for name in ("store_in_env", "set_env_writer", "_builtin_env_write", "_hermes_env_writer"):
            self.assertFalse(hasattr(credentials, name), name)
        src = Path(credentials.__file__).read_text()
        self.assertNotIn("save_env_value", src)
        self.assertNotIn("env_file(", src)


class Credentials(unittest.TestCase):
    def tearDown(self):
        credentials.set_secret_getter(None)
        credentials.set_op_runner(None)
        submodule("core.redact").forget_all()

    def test_env_ref_uses_secret_getter(self):
        credentials.set_secret_getter(lambda n: SECRET if n == "WPM_A_APP_PASSWORD" else None)
        self.assertEqual(credentials.resolve({"id": "a", "username": "u"}), SECRET)

    def test_missing_secret_message_has_no_value(self):
        credentials.set_secret_getter(lambda n: None)
        with self.assertRaises(credentials.CredentialError) as cm:
            credentials.resolve({"id": "a", "username": "u", "credential": {"env": "WPM_A_APP_PASSWORD"}})
        self.assertIn("hermes secrets onepassword set WPM_A_APP_PASSWORD", str(cm.exception))

    def test_secret_scope_error_is_wrapped(self):
        def boom(name):
            raise RuntimeError(f"no scope for {name}")
        credentials.set_secret_getter(boom)
        with self.assertRaises(credentials.CredentialError):
            credentials.resolve({"id": "a", "username": "u"})

    def test_op_runner_uses_argument_list_no_shell(self):
        seen = {}

        def fake_run(argv, **kw):
            seen["argv"], seen["kw"] = argv, kw
            return subprocess.CompletedProcess(argv, 0, stdout=SECRET, stderr="")

        with mock.patch.object(credentials.shutil, "which", return_value="/usr/bin/op"), \
             mock.patch.object(credentials.subprocess, "run", side_effect=fake_run):
            pw = credentials.resolve({"id": "a", "username": "u", "credential": {"op": "op://V/I/f"}}, "op")
        self.assertEqual(pw, SECRET)
        self.assertEqual(seen["argv"], ["/usr/bin/op", "read", "--no-newline", "op://V/I/f"])
        self.assertNotIn("shell", seen["kw"])
        self.assertEqual(seen["kw"]["timeout"], credentials.OP_TIMEOUT)

    def test_op_failure_is_scrubbed(self):
        def fake_run(argv, **kw):
            return subprocess.CompletedProcess(argv, 1, stdout="", stderr=f"[ERROR] leaked {SECRET}")

        with mock.patch.object(credentials.shutil, "which", return_value="/usr/bin/op"), \
             mock.patch.object(credentials.subprocess, "run", side_effect=fake_run):
            with self.assertRaises(credentials.CredentialError) as cm:
                credentials.resolve({"id": "a", "username": "u", "credential": {"op": "op://V/I/f"}})
        self.assertNotIn(SECRET, str(cm.exception))

    def test_op_timeout(self):
        with mock.patch.object(credentials.shutil, "which", return_value="/usr/bin/op"), \
             mock.patch.object(credentials.subprocess, "run",
                               side_effect=subprocess.TimeoutExpired(["op"], credentials.OP_TIMEOUT)):
            with self.assertRaises(credentials.CredentialError) as cm:
                credentials.resolve({"id": "a", "username": "u", "credential": {"op": "op://V/I/f"}})
        self.assertIn("timed out", str(cm.exception))

    def test_bad_op_binary_setting(self):
        with self.assertRaises(credentials.CredentialError):
            credentials.resolve({"id": "a", "username": "u", "credential": {"op": "op://V/I/f"}}, "op; rm -rf /")

    def test_connection_string_roundtrip(self):
        d = credentials.decode_connection_string(connection_string())
        self.assertEqual((d["url"], d["user"], d["app_password"]), (BASE, USER, SECRET))
        # line-wrapped / urlsafe variants still decode
        s = connection_string().replace("+", "-").replace("/", "_")
        self.assertEqual(credentials.decode_connection_string(s[:20] + "\n" + s[20:])["user"], USER)
        for bad in ("hello", "wpmh1:", "wpmh1:!!!!", "wpmh2:abc", None):
            with self.assertRaises(credentials.CredentialError):
                credentials.decode_connection_string(bad)


class Settings(unittest.TestCase):
    def tearDown(self):
        settings.set_getter(None)

    def test_defaults_and_clamping(self):
        s = settings.current()
        self.assertEqual((s.require_approval, s.op_binary, s.request_timeout, s.max_parallel), (True, "op", 60, 4))
        self.assertEqual(s.update_timeout, 300)
        settings.set_getter(lambda k, d=None: {"require_approval": "false", "request_timeout": 900,
                                               "max_parallel": 0}.get(k, d))
        s = settings.current()
        self.assertFalse(s.require_approval)
        self.assertEqual(s.request_timeout, 600)
        self.assertEqual(s.update_timeout, 600)
        self.assertEqual(s.max_parallel, 1)


class Normalizers(unittest.TestCase):
    def test_updates_tolerates_dict_and_wrapped_shapes(self):
        raw = {"updates": {"plugins": {"akismet/akismet.php": {"Name": "Akismet", "Version": "5.0",
                                                               "new_version": "5.3"}}}}
        out = connector.normalize_updates(raw)
        self.assertEqual(out["plugins"][0]["id"], "akismet/akismet.php")
        self.assertEqual(out["counts"]["plugins"], 1)

    def test_garbage(self):
        self.assertEqual(connector.normalize_updates("nope")["counts"]["total"], 0)
        self.assertEqual(connector.normalize_status(None)["update_counts"]["total"], 0)
        self.assertEqual(connector.normalize_run([])["result"], "unknown")
        self.assertFalse(connector.normalize_run({})["all_succeeded"])


class RealConnectorSamples(unittest.TestCase):
    """Responses captured from the real connector in WordPress Playground (e2e run)."""

    DIR = Path(__file__).resolve().parent / "fixtures" / "connector-samples"

    def body(self, name):
        return json.loads((self.DIR / f"{name}.json").read_text())["body"]

    def test_status(self):
        s = connector.normalize_status(self.body("status"))
        self.assertEqual(s["connector_version"], "0.1.0")
        self.assertTrue(s["wp_version"])
        self.assertTrue(s["user_capabilities"]["update_plugins"])

    def test_updates_refresh(self):
        u = connector.normalize_updates(self.body("updates-refresh"))
        ids = [i["id"] for i in u["plugins"]]
        self.assertIn("hello-dolly/hello.php", ids)
        hello = [i for i in u["plugins"] if i["id"] == "hello-dolly/hello.php"][0]
        self.assertEqual((hello["current_version"], hello["new_version"]), ("1.6", "1.7.2"))
        self.assertEqual(u["counts"]["plugins"], len(u["plugins"]))

    def test_partial_plugin_run(self):
        r = connector.normalize_run(self.body("updates-post-plugin"))
        self.assertEqual(r["result"], "partial")
        self.assertFalse(r["all_succeeded"])
        bad = [x for x in r["results"] if not x["success"]]
        self.assertEqual(bad[0]["status"], "not_installed")

    def test_theme_and_core_runs(self):
        self.assertTrue(connector.normalize_run(self.body("updates-post-theme"))["all_succeeded"])
        core = connector.normalize_run(self.body("updates-post-core-uptodate"))
        self.assertEqual(core["results"][0]["status"], "up_to_date")

    def test_health(self):
        h = connector.normalize_health(self.body("health"))
        self.assertEqual(h["status"], "ok")
        self.assertTrue(h["database_reachable"])

    def test_lock_and_auth_errors_map(self):
        env = Env().start()
        try:
            for name, kind in (("updates-post-locked", "conflict"), ("error-wrong-password", "auth")):
                sample = json.loads((self.DIR / f"{name}.json").read_text())
                env.transport.responses["GET /wp-json/website-manager-hermes/v1/status"] = [
                    {"status": sample["http_status"], "body": sample["body"], "headers": {}}]
                c = client_mod.WPClient({"id": "s", "url": BASE, "username": USER}, SECRET)
                with self.assertRaises(client_mod.WPError) as cm:
                    c.connector("GET", "status")
                self.assertEqual(cm.exception.kind, kind, name)
        finally:
            env.stop()


class Client(unittest.TestCase):
    def setUp(self):
        self.env = Env().start()
        self.site = {"id": "s", "url": BASE, "username": USER}

    def tearDown(self):
        self.env.stop()

    def test_rest_route_fallback_without_pretty_permalinks(self):
        t = self.env.transport
        t.responses["GET /wp-json/website-manager-hermes/v1/status"] = [
            {"status": 404, "body": "<html>Not Found</html>", "headers": {"content-type": "text/html"}}]
        t.responses["GET /"] = ReplayTransport().responses["GET /wp-json/website-manager-hermes/v1/status"]
        c = client_mod.WPClient(self.site, SECRET)
        data = c.connector("GET", "status")
        self.assertEqual(data["wp_version"], "6.8.1")
        self.assertTrue(c.use_rest_route)
        self.assertIn("rest_route=%2Fwebsite-manager-hermes%2Fv1%2Fstatus", t.calls[-1].url)

    def test_non_json_is_explained(self):
        self.env.transport.responses["GET /wp-json/website-manager-hermes/v1/status"] = [
            {"status": 200, "body": "<html>Maintenance</html>", "headers": {}}]
        with self.assertRaises(client_mod.WPError) as cm:
            client_mod.WPClient(self.site, SECRET).connector("GET", "status")
        self.assertEqual(cm.exception.kind, "bad_response")

    def test_php_notice_before_json_recovered(self):
        good = json.dumps(ReplayTransport().responses["GET /wp-json/website-manager-hermes/v1/status"][0]["body"])
        self.env.transport.responses["GET /wp-json/website-manager-hermes/v1/status"] = [
            {"status": 200, "body": "<br />\n<b>Notice</b>: something\n" + good, "headers": {}}]
        self.assertEqual(client_mod.WPClient(self.site, SECRET).connector("GET", "status")["php_version"], "8.2.20")

    def test_insecure_client_refused(self):
        with self.assertRaises(client_mod.WPError):
            client_mod.WPClient({"id": "s", "url": "http://x.test", "username": "u"}, SECRET)


if __name__ == "__main__":
    unittest.main()
