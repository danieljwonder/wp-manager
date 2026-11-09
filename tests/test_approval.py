import os
import unittest
from unittest import mock

from helpers import BASE, SECRET, USER, Env, ReplayTransport, add_default_site, connection_string, submodule

approval = submodule("approval")


class Hook(unittest.TestCase):
    settings = {}

    def setUp(self):
        self.env = Env(settings=self.settings).start()
        self.t = self.env.transport
        add_default_site(self.env)

    def tearDown(self):
        self.env.stop()

    def hook(self, tool, args):
        return approval.pre_tool_call(tool_name=tool, args=args, task_id="t", session_id="s")

    # ---------------------------------------------------------------- updates
    def test_update_apply_requires_approval_with_narrow_rule(self):
        d = self.hook("wp_update_apply", {"site": "wp-example", "type": "plugin", "items": ["hello-dolly/hello.php"]})
        self.assertEqual(d["action"], "approve")
        self.assertEqual(d["rule_key"], "wp-manager:update:wp-example")
        self.assertIn("hello-dolly/hello.php", d["message"])
        self.assertIn("no backups", d["message"])
        self.assertIn(BASE, d["message"])

    def test_update_rule_is_per_site(self):
        self.env.secrets["WPM_OTHER_APP_PASSWORD"] = SECRET
        submodule("core.registry").add_site({"id": "other", "label": "Other", "url": "https://other.test",
                                             "username": USER, "tags": [], "credential": {"env": "WPM_OTHER_APP_PASSWORD"}})
        d = self.hook("wp_update_apply", {"site": "other", "type": "core"})
        self.assertEqual(d["rule_key"], "wp-manager:update:other")

    def test_update_hook_makes_no_network_call(self):
        before = self.t.network_calls
        self.hook("wp_update_apply", {"site": "wp-example", "type": "plugin", "items": "all"})
        self.assertEqual(self.t.network_calls, before)

    # ---------------------------------------------------------------- content
    def test_draft_create_not_gated(self):
        self.assertIsNone(self.hook("wp_content_save", {"site": "wp-example", "type": "post", "title": "x"}))

    def test_publish_gated(self):
        d = self.hook("wp_content_save", {"site": "wp-example", "type": "post", "title": "x", "status": "publish"})
        self.assertEqual(d["action"], "approve")
        self.assertEqual(d["rule_key"], "wp-manager:publish:wp-example")

    def test_edit_published_gated(self):
        d = self.hook("wp_content_save", {"site": "wp-example", "type": "post", "id": 12, "title": "new"})
        self.assertEqual(d["action"], "approve")
        self.assertIn("already-published", d["message"])

    def test_unpublishing_a_published_item_is_gated(self):
        d = self.hook("wp_content_save", {"site": "wp-example", "type": "post", "id": 12, "status": "draft"})
        self.assertEqual(d["action"], "approve")
        self.assertIn("draft", d["message"])

    def test_edit_draft_not_gated(self):
        self.assertIsNone(self.hook("wp_content_save", {"site": "wp-example", "type": "post", "id": 15, "title": "y"}))

    def test_status_lookup_failure_fails_closed(self):
        self.t.base = "https://unreachable.invalid"
        d = self.hook("wp_content_save", {"site": "wp-example", "type": "post", "id": 15, "title": "y"})
        self.assertEqual(d["action"], "approve")
        self.assertIn("could not be checked", d["message"])

    # ---------------------------------------------------------------- site add
    def test_site_add_default_secret_gated_with_url_and_secret_name(self):
        d = self.hook("wp_site_add", {"url": "https://new.test", "username": "a", "id": "new"})
        self.assertEqual(d["action"], "approve")
        self.assertEqual(d["rule_key"], "wp-manager:credential:new")
        self.assertIn("https://new.test", d["message"])
        self.assertIn("WPM_NEW_APP_PASSWORD", d["message"])

    def test_site_add_colliding_id_refused_before_any_prompt(self):
        # wp-example and wp--example both map to WPM_WP_EXAMPLE_APP_PASSWORD.
        args = {"url": "https://evil.test", "username": "a", "id": "wp--example"}
        with self.assertRaises(submodule("core.service").ServiceError) as cm:
            submodule("core.service").plan_add(args)
        self.assertIn("WPM_WP_EXAMPLE_APP_PASSWORD", str(cm.exception))
        before = self.t.network_calls
        self.assertIsNone(self.hook("wp_site_add", args))  # invalid: the handler refuses it
        r = __import__("json").loads(submodule("tools").wp_site_add(args))
        self.assertFalse(r["success"])
        self.assertEqual(self.t.network_calls, before)
        self.assertEqual([s["id"] for s in submodule("core.registry").list_sites()], ["wp-example"])

    def test_my_blog_vs_my__blog(self):
        reg, svc = submodule("core.registry"), submodule("core.service")
        self.assertEqual(reg.default_env_name("my-blog"), reg.default_env_name("my--blog"))
        reg.add_site({"id": "my-blog", "label": "B", "url": "https://blog.test", "username": "a", "tags": [],
                      "credential": {"env": reg.default_env_name("my-blog")}})
        with self.assertRaises(svc.ServiceError):
            svc.plan_add({"url": "https://other.test", "username": "a", "id": "my--blog"})

    def test_remove_then_readd_with_new_url_is_gated(self):
        svc = submodule("core.service")
        svc.remove_site("wp-example", actor="test")
        d = self.hook("wp_site_add", {"url": "https://attacker.test", "username": "a", "id": "wp-example"})
        self.assertEqual(d["action"], "approve")
        self.assertIn("https://attacker.test", d["message"])
        self.assertIn("WPM_WP_EXAMPLE_APP_PASSWORD", d["message"])

    def test_allow_insecure_gated_with_its_own_rule(self):
        d = self.hook("wp_site_add", {"url": "http://plain.test", "username": "a", "id": "plain",
                                      "allow_insecure": True})
        self.assertEqual(d["action"], "approve")
        self.assertEqual(d["rule_key"], "wp-manager:credential:plain:insecure")
        self.assertIn("unencrypted", d["message"])

    def test_site_add_connection_string_without_storage_refused(self):
        self.assertIsNone(self.hook("wp_site_add", {"connection_string": connection_string("https://new.test"),
                                                    "id": "new"}))

    def test_site_add_binding_foreign_secret_gated(self):
        d = self.hook("wp_site_add", {"url": "https://evil.test", "username": "a", "id": "evil",
                                      "credential": {"env": "WPM_WP_EXAMPLE_APP_PASSWORD"}})
        self.assertEqual(d["action"], "approve")
        self.assertEqual(d["rule_key"], "wp-manager:credential:evil")
        d = self.hook("wp_site_add", {"url": "https://evil.test", "username": "a", "id": "evil",
                                      "credential": {"op": "op://Private/WP/password"}})
        self.assertEqual(d["action"], "approve")

    # ---------------------------------------------------------------- general
    def test_other_tools_exempt(self):
        for tool in ("wp_sites", "wp_updates_scan", "wp_health_check", "wp_content_get", "wp_content_list",
                     "wp_site_remove", "terminal", "web_search"):
            self.assertIsNone(self.hook(tool, {"site": "wp-example"}), tool)

    def test_invalid_args_not_gated_and_no_network(self):
        before = self.t.network_calls
        self.assertIsNone(self.hook("wp_update_apply", {"site": "nope", "type": "plugin"}))
        self.assertIsNone(self.hook("wp_update_apply", "garbage"))
        self.assertIsNone(self.hook("wp_content_save", {"site": "wp-example", "type": "post", "id": "x"}))
        self.assertEqual(self.t.network_calls, before)

    def test_cron_fails_closed(self):
        approval.set_cron_probe(lambda: object())
        try:
            d = self.hook("wp_update_apply", {"site": "wp-example", "type": "plugin"})
        finally:
            approval.set_cron_probe(None)
        self.assertEqual(d["action"], "block")
        self.assertIn("unattended", d["message"])

    def test_cron_env_fails_closed(self):
        with mock.patch.dict(os.environ, {"HERMES_CRON_SESSION": "1"}):
            d = self.hook("wp_content_save", {"site": "wp-example", "type": "post", "title": "x", "status": "publish"})
        self.assertEqual(d["action"], "block")

    def test_webhook_platform_fails_closed(self):
        with mock.patch.dict(os.environ, {"HERMES_SESSION_PLATFORM": "webhook"}):
            d = self.hook("wp_update_apply", {"site": "wp-example", "type": "plugin"})
        self.assertEqual(d["action"], "block")

    def test_cron_still_allows_ungated_calls(self):
        approval.set_cron_probe(lambda: object())
        try:
            self.assertIsNone(self.hook("wp_content_save", {"site": "wp-example", "type": "post", "title": "x"}))
        finally:
            approval.set_cron_probe(None)


class ApprovalOff(unittest.TestCase):
    def setUp(self):
        self.env = Env(settings={"require_approval": False}).start()
        add_default_site(self.env)

    def tearDown(self):
        self.env.stop()

    def hook(self, tool, args):
        return approval.pre_tool_call(tool_name=tool, args=args)

    def test_gated_calls_go_through(self):
        self.assertIsNone(self.hook("wp_update_apply", {"site": "wp-example", "type": "plugin"}))
        self.assertIsNone(self.hook("wp_content_save", {"site": "wp-example", "type": "post", "title": "x",
                                                        "status": "publish"}))

    def test_default_secret_add_still_gated(self):
        d = self.hook("wp_site_add", {"url": "https://new.test", "username": "a", "id": "new"})
        self.assertEqual(d["action"], "approve")

    def test_credential_binding_still_gated(self):
        d = self.hook("wp_site_add", {"url": "https://evil.test", "username": "a", "id": "evil",
                                      "credential": {"env": "WPM_WP_EXAMPLE_APP_PASSWORD"}})
        self.assertEqual(d["action"], "approve")


if __name__ == "__main__":
    unittest.main()
