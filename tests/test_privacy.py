"""Privacy: the Application Password never appears in tool output, logs, state, the audit log or
errors; URL userinfo and Authorization values are scrubbed; credentials never follow a redirect
to another host; plain http is refused."""

import json
import logging
import unittest

from helpers import (BASE, SECRET, SECRET_NOSPACE, USER, Env, ReplayTransport, add_default_site, call,
                     connection_string, submodule)

tools = submodule("tools")
redact = submodule("core.redact")
client_mod = submodule("core.client")
approval = submodule("approval")


def b64_basic():
    import base64

    return base64.b64encode(f"{USER}:{SECRET_NOSPACE}".encode()).decode()


FORBIDDEN = (SECRET, SECRET_NOSPACE)


class EverythingClean(unittest.TestCase):
    def setUp(self):
        self.env = Env().start()
        self.t = self.env.transport
        self.outputs = []
        self.log_records = []
        handler = logging.Handler()
        handler.emit = lambda rec: self.log_records.append(rec.getMessage())
        self.handler = handler
        logging.getLogger().addHandler(handler)
        logging.getLogger().setLevel(logging.DEBUG)

    def tearDown(self):
        logging.getLogger().removeHandler(self.handler)
        self.env.stop()

    def run_tool(self, handler, args):
        out = handler(args)
        self.outputs.append(out)
        return json.loads(out)

    def assert_clean(self):
        blobs = list(self.outputs) + self.log_records
        files = self.env.all_files_text()
        for name, text in files.items():
            if name == ".env":
                continue  # the one place the user asked us to store it
            blobs.append(text)
        basic = b64_basic()
        for blob in blobs:
            for secret in FORBIDDEN + (basic,):
                self.assertNotIn(secret, blob)
            self.assertNotIn("wpmh1:", blob.replace("wpmh1:[redacted]", ""))

    def test_full_workflow_never_leaks(self):
        r = self.run_tool(tools.wp_site_add, {"connection_string": connection_string(), "id": "wp-example", "storage": "env"})
        self.assertTrue(r["success"], r)
        self.run_tool(tools.wp_sites, {})
        self.run_tool(tools.wp_updates_scan, {"refresh": True})
        self.run_tool(tools.wp_update_apply, {"site": "wp-example", "type": "plugin", "items": ["hello-dolly"]})
        self.run_tool(tools.wp_health_check, {"site": "wp-example"})
        self.run_tool(tools.wp_content_list, {"site": "wp-example", "type": "post"})
        self.run_tool(tools.wp_content_get, {"site": "wp-example", "type": "post", "id": 12})
        self.run_tool(tools.wp_content_save, {"site": "wp-example", "type": "post", "title": "t"})
        self.run_tool(tools.wp_site_remove, {"site": "wp-example"})
        self.assert_clean()
        self.assertTrue((self.env.home / "wp-manager" / "actions.jsonl").exists())

    def test_hook_messages_clean(self):
        add_default_site(self.env)
        d = approval.pre_tool_call(tool_name="wp_site_add",
                                   args={"connection_string": connection_string("https://x.test"), "id": "x"})
        self.assertIsNone(d)
        d = approval.pre_tool_call(tool_name="wp_update_apply", args={"site": "wp-example", "type": "plugin"})
        self.outputs.append(json.dumps(d))
        self.assert_clean()

    def test_site_echoing_secret_is_redacted(self):
        """A hostile/buggy site that reflects the password in an error body must not leak it."""
        add_default_site(self.env)
        body = {"code": "rest_forbidden", "message": f"bad password {SECRET} for {USER}", "data": {"status": 403}}
        self.t.responses["GET /wp-json/website-manager-hermes/v1/status"] = [
            {"status": 403, "body": body, "headers": {}}]
        r = self.run_tool(tools.wp_updates_scan, {})
        self.assertEqual(r["sites"][0]["kind"], "forbidden")
        self.assert_clean()

    def test_content_reflecting_secret_is_redacted(self):
        add_default_site(self.env)
        post = json.loads(json.dumps(submodule("core.client")._decode_json(
            json.dumps(self.t.responses["GET /wp-json/wp/v2/posts/12"][0]["body"]).encode())))
        post["content"]["raw"] = f"oops {SECRET_NOSPACE}"
        self.t.responses["GET /wp-json/wp/v2/posts/12"] = [{"status": 200, "body": post, "headers": {}}]
        self.run_tool(tools.wp_content_get, {"site": "wp-example", "type": "post", "id": 12})
        self.assert_clean()

    def test_connection_string_errors_do_not_echo(self):
        bad = "wpmh1:" + "A" * 40 + "!!"
        r = self.run_tool(tools.wp_site_add, {"connection_string": bad})
        self.assertFalse(r["success"])
        self.assertNotIn("AAAA", r["error"])
        self.assertEqual(self.t.network_calls, 0)


class Scrubbing(unittest.TestCase):
    def tearDown(self):
        redact.forget_all()

    def test_url_userinfo(self):
        s = redact.scrub("failed: https://admin:hunter2pass@example.com/wp-json/ refused")
        self.assertNotIn("hunter2pass", s)
        self.assertNotIn("admin:", s)
        self.assertIn("https://[redacted]@example.com", s)

    def test_authorization_header(self):
        s = redact.scrub('headers={"Authorization": "Basic YWRtaW46c2VjcmV0c2VjcmV0", "Accept": "x"}')
        self.assertNotIn("YWRtaW46c2VjcmV0c2VjcmV0", s)
        s = redact.scrub("Authorization: Bearer abc.def.ghijklmnop")
        self.assertNotIn("abc.def.ghijklmnop", s)

    def test_app_password_shape(self):
        self.assertNotIn(SECRET, redact.scrub(f"x {SECRET} y"))

    def test_known_secret_exact(self):
        redact.remember("zzzSuperSecretzzz", "bob")
        self.assertEqual(redact.scrub_known("a zzzSuperSecretzzz b"), "a [redacted] b")

    def test_wperror_messages_scrubbed(self):
        e = client_mod.WPError("could not connect to https://u:p4ssw0rd!@h.test/")
        self.assertNotIn("p4ssw0rd", str(e))


class Transport(unittest.TestCase):
    def setUp(self):
        self.env = Env().start()
        add_default_site(self.env)
        self.t = self.env.transport

    def tearDown(self):
        self.env.stop()

    def test_cross_host_redirect_does_not_carry_credentials(self):
        self.t.responses["GET /wp-json/website-manager-hermes/v1/status"] = [
            {"status": 301, "body": "", "headers": {"location": "https://evil.test/wp-json/website-manager-hermes/v1/status"}}]
        before = len(self.t.calls)
        r = call(tools.wp_updates_scan, {})
        self.assertEqual(r["sites"][0]["kind"], "redirect")
        self.assertFalse(any("evil.test" in c.url for c in self.t.calls[before:]))

    def test_same_origin_redirect_followed(self):
        self.t.responses["GET /wp-json/website-manager-hermes/v1/status"] = [
            {"status": 301, "body": "", "headers": {"location": BASE + "/index.php/wp-json/website-manager-hermes/v1/status"}}]
        self.t.responses["GET /index.php/wp-json/website-manager-hermes/v1/status"] = \
            ReplayTransport().responses["GET /wp-json/website-manager-hermes/v1/status"]
        r = call(tools.wp_updates_scan, {})
        self.assertTrue(r["sites"][0]["ok"], r)

    def test_https_to_http_redirect_refused(self):
        self.t.responses["GET /wp-json/website-manager-hermes/v1/status"] = [
            {"status": 302, "body": "", "headers": {"location": "http://wp.example.test/wp-json/x"}}]
        r = call(tools.wp_updates_scan, {})
        self.assertFalse(r["sites"][0]["ok"])

    def test_plain_http_refused_without_network(self):
        before = self.t.network_calls
        r = call(tools.wp_site_add, {"url": "http://shop.example.com", "username": "a", "id": "shop"})
        self.assertFalse(r["success"])
        self.assertEqual(r["kind"], "insecure")
        self.assertEqual(self.t.network_calls, before)

    def test_plain_http_allowed_when_opted_in(self):
        self.env.secrets["WPM_SHOP_APP_PASSWORD"] = SECRET
        t = ReplayTransport(base="http://shop.example.com")
        submodule("core.client").set_transport(t)
        r = call(tools.wp_site_add, {"url": "http://shop.example.com", "username": "a", "id": "shop",
                                     "allow_insecure": True})
        self.assertTrue(r["success"], r)
        self.assertIn("unencrypted", r["warning"])

    def test_localhost_http_allowed(self):
        self.env.secrets["WPM_LOCAL_APP_PASSWORD"] = SECRET
        submodule("core.client").set_transport(ReplayTransport(base="http://127.0.0.1:9400"))
        r = call(tools.wp_site_add, {"url": "http://127.0.0.1:9400", "username": "a", "id": "local"})
        self.assertTrue(r["success"], r)
        self.assertNotIn("warning", r)

    def test_userinfo_in_url_rejected(self):
        before = self.t.network_calls
        r = call(tools.wp_site_add, {"url": "https://admin:pw12345678@x.test", "username": "a", "id": "x"})
        self.assertFalse(r["success"])
        self.assertNotIn("pw12345678", json.dumps(r))
        self.assertEqual(self.t.network_calls, before)

    def test_unrelated_env_secret_refused(self):
        before = self.t.network_calls
        r = call(tools.wp_site_add, {"url": "https://x.test", "username": "a", "id": "x",
                                     "credential": {"env": "OPENAI_API_KEY"}})
        self.assertFalse(r["success"])
        self.assertIn("WPM_", r["error"])
        self.assertEqual(self.t.network_calls, before)


if __name__ == "__main__":
    unittest.main()
