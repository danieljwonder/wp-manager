import json
import unittest

from helpers import BASE, SECRET, USER, Env, ReplayTransport, add_default_site, call, connection_string, submodule

tools = submodule("tools")
state = submodule("core.state")
registry = submodule("core.registry")


class Base(unittest.TestCase):
    variants = ()
    settings = {}

    def setUp(self):
        self.env = Env(transport=ReplayTransport(variants=self.variants), settings=self.settings).start()
        self.t = self.env.transport

    def tearDown(self):
        self.env.stop()


class Sites(Base):
    def test_empty_list_has_hint(self):
        r = call(tools.wp_sites)
        self.assertTrue(r["success"])
        self.assertEqual(r["count"], 0)
        self.assertIn("connection string", r["hint"])

    def test_add_with_env_ref_verifies_status(self):
        r = add_default_site(self.env)
        self.assertTrue(r["success"], r)
        self.assertEqual(r["site"]["id"], "wp-example")
        self.assertEqual(r["verified"]["wp_version"], "6.8.1")
        self.assertIn("nothing was written", r["credential_storage"])
        self.assertEqual(self.env.env_writes, [])
        auth = [c for c in self.t.calls if c.headers.get("Authorization")]
        self.assertTrue(auth, "status call must be authenticated")
        r = call(tools.wp_sites)
        self.assertEqual(r["count"], 1)
        self.assertEqual(r["sites"][0]["wp_version"], "6.8.1")
        self.assertEqual(r["sites"][0]["credential"]["type"], "env")

    def test_add_missing_secret_explains_and_makes_no_network_call(self):
        r = call(tools.wp_site_add, {"url": BASE, "username": USER, "id": "wp-example"})
        self.assertFalse(r["success"])
        self.assertIn("hermes secrets onepassword set WPM_WP_EXAMPLE_APP_PASSWORD", r["error"])
        self.assertEqual(self.t.network_calls, 0)

    def test_add_with_connection_string_stores_env_only_when_selected(self):
        r = call(tools.wp_site_add, {"connection_string": connection_string(), "id": "wp-example", "storage": "env"})
        self.assertTrue(r["success"], r)
        self.assertEqual(self.env.env_writes, ["WPM_WP_EXAMPLE_APP_PASSWORD"])
        self.assertIn("WPM_WP_EXAMPLE_APP_PASSWORD", r["credential_storage"])
        dotenv = (self.env.home / ".env").read_text()
        self.assertIn("WPM_WP_EXAMPLE_APP_PASSWORD=", dotenv)
        self.assertEqual((self.env.home / ".env").stat().st_mode & 0o777, 0o600)
        sites_json = (self.env.home / "wp-manager" / "sites.json").read_text()
        self.assertNotIn(SECRET.replace(" ", ""), sites_json)
        self.assertEqual(json.loads(sites_json)["sites"][0]["credential"], {"env": "WPM_WP_EXAMPLE_APP_PASSWORD"})

    def test_connection_string_with_explicit_ref_is_not_stored(self):
        self.env.secrets["WPM_WP_EXAMPLE_APP_PASSWORD"] = SECRET
        r = call(tools.wp_site_add, {"connection_string": connection_string(), "id": "wp-example",
                                     "credential": {"env": "WPM_WP_EXAMPLE_APP_PASSWORD"}})
        self.assertTrue(r["success"], r)
        self.assertEqual(self.env.env_writes, [])
        self.assertIn("not stored", r["note"])

    def test_add_with_op_ref(self):
        self.env.secrets["op://Private/WP/password"] = SECRET
        r = call(tools.wp_site_add, {"url": BASE, "username": USER, "id": "wp-example",
                                     "credential": {"op": "op://Private/WP/password"}})
        self.assertTrue(r["success"], r)
        self.assertEqual(self.env.op_calls, [("op", "op://Private/WP/password")])
        self.assertIn("1Password", r["credential_storage"])

    def test_duplicate_rejected_before_network(self):
        add_default_site(self.env)
        before = self.t.network_calls
        r = add_default_site(self.env)
        self.assertFalse(r["success"])
        self.assertEqual(self.t.network_calls, before)

    def test_connector_missing(self):
        self.t.responses.update(ReplayTransport(variants=("connector_missing",)).responses)
        r = add_default_site(self.env)
        self.assertFalse(r["success"])
        self.assertEqual(r["kind"], "connector_missing")
        self.assertEqual(registry.list_sites(), [])

    def test_bad_auth(self):
        self.t.responses.update(ReplayTransport(variants=("bad_auth",)).responses)
        r = add_default_site(self.env)
        self.assertFalse(r["success"])
        self.assertEqual(r["kind"], "auth")

    def test_remove(self):
        add_default_site(self.env)
        r = call(tools.wp_site_remove, {"site": "wp-example"})
        self.assertTrue(r["success"], r)
        self.assertIn("not revoked", r["note"])
        self.assertEqual(call(tools.wp_sites)["count"], 0)
        actions = state.read_actions()
        self.assertEqual([a["action"] for a in actions], ["site_remove", "site_add"])

    def test_lookup_by_url_and_label(self):
        add_default_site(self.env)
        self.assertEqual(registry.get_site(BASE + "/")["id"], "wp-example")
        self.assertEqual(registry.get_site("example")["id"], "wp-example")


class Scan(Base):
    def setUp(self):
        super().setUp()
        add_default_site(self.env)

    def test_scan_all(self):
        r = call(tools.wp_updates_scan, {})
        self.assertTrue(r["success"], r)
        self.assertEqual(r["scanned"], 1)
        self.assertEqual(r["totals"], {"plugins": 2, "themes": 1, "core": 1, "translations": 1, "total": 5})
        plugin_ids = [p["id"] for p in r["sites"][0]["updates"]["plugins"]]
        self.assertIn("hello-dolly/hello.php", plugin_ids)
        site = call(tools.wp_sites)["sites"][0]
        self.assertEqual(site["pending"]["plugins"], 2)
        self.assertIsNotNone(site["last_checked"])

    def test_refresh_hits_refresh_endpoint(self):
        call(tools.wp_updates_scan, {"refresh": True})
        self.assertTrue(self.t.requests_to("updates?refresh=1"))

    def test_tag_filter(self):
        r = call(tools.wp_updates_scan, {"tag": "nobody"})
        self.assertEqual(r["scanned"], 0)

    def test_unknown_site_zero_network(self):
        before = self.t.network_calls
        r = call(tools.wp_updates_scan, {"sites": ["nope"]})
        self.assertFalse(r["success"])
        self.assertEqual(self.t.network_calls, before)

    def test_scan_failure_is_per_site(self):
        self.t.responses.update(ReplayTransport(variants=("bad_auth",)).responses)
        r = call(tools.wp_updates_scan, {})
        self.assertTrue(r["success"])
        self.assertEqual(r["failed"], 1)
        self.assertEqual(r["sites"][0]["kind"], "auth")
        self.assertIn("message", state.get("wp-example")["last_error"])


class Apply(Base):
    def setUp(self):
        super().setUp()
        add_default_site(self.env)
        call(tools.wp_updates_scan, {})

    def test_apply_plugin_ok(self):
        r = call(tools.wp_update_apply, {"site": "wp-example", "type": "plugin", "items": ["hello-dolly"]})
        self.assertTrue(r["success"], r)
        self.assertEqual(r["results"][0]["from_version"], "1.6")
        self.assertEqual(r["results"][0]["to_version"], "1.7.2")
        self.assertEqual(r["health"]["verdict"], "ok")
        self.assertNotIn("alert", r)
        post = [c for c in self.t.calls if c.method == "POST"][0]
        self.assertEqual(json.loads(post.body), {"type": "plugin", "items": ["hello-dolly/hello.php"]})
        self.assertGreaterEqual(post.timeout, 300)
        a = state.read_actions("wp-example")[0]
        self.assertEqual(a["action"], "update_apply")
        self.assertEqual(a["health"], "ok")

    def test_apply_all_sends_empty_items(self):
        r = call(tools.wp_update_apply, {"site": "wp-example", "type": "theme", "items": "all"})
        self.assertTrue(r["success"], r)
        post = [c for c in self.t.calls if c.method == "POST"][0]
        self.assertEqual(json.loads(post.body)["items"], [])

    def test_all_with_exclude_expands_from_fresh_list(self):
        r = call(tools.wp_update_apply, {"site": "wp-example", "type": "plugin", "items": "all",
                                         "exclude": ["classic-editor"]})
        self.assertTrue(r["success"], r)
        post = [c for c in self.t.calls if c.method == "POST"][0]
        self.assertEqual(json.loads(post.body)["items"], ["hello-dolly/hello.php"])

    def test_degraded_health_never_reports_success(self):
        self.t.responses.update(ReplayTransport(variants=("health_degraded",)).responses)
        r = call(tools.wp_update_apply, {"site": "wp-example", "type": "plugin", "items": ["hello-dolly/hello.php"]})
        # The connector says the update itself worked, but a failed health check means no success.
        self.assertEqual(r["update_result"], "success")
        self.assertIs(r["success"], False)
        self.assertEqual(r["health"]["verdict"], "degraded")
        self.assertIn("HEALTH CHECK DEGRADED", r["alert"])
        self.assertIn("hello-dolly/hello.php", r["alert"])
        self.assertEqual(state.read_actions("wp-example")[0]["result"], "problem")

    def test_home_critical_error_is_down(self):
        self.t.responses.update(ReplayTransport(variants=("home_critical",)).responses)
        r = call(tools.wp_update_apply, {"site": "wp-example", "type": "plugin", "items": ["hello-dolly/hello.php"]})
        self.assertEqual(r["health"]["verdict"], "down")
        self.assertIn("HEALTH CHECK DOWN", r["alert"])

    def test_lock_conflict_surfaces_and_checks_health(self):
        self.t.responses.update(ReplayTransport(variants=("locked",)).responses)
        r = call(tools.wp_update_apply, {"site": "wp-example", "type": "plugin", "items": ["hello-dolly/hello.php"]})
        self.assertFalse(r["success"])
        self.assertEqual(r["kind"], "conflict")
        self.assertIn("Health check after the failure", r["error"])

    def test_invalid_type_zero_network(self):
        before = self.t.network_calls
        for bad in ({"site": "wp-example", "type": "everything"},
                    {"site": "wp-example", "type": "plugin", "items": ["../../wp-config.php"]},
                    {"site": "wp-example", "type": "plugin", "items": [{"x": 1}]},
                    {"site": "missing", "type": "plugin"},
                    {"type": "plugin"}):
            r = call(tools.wp_update_apply, bad)
            self.assertFalse(r["success"], bad)
        self.assertEqual(self.t.network_calls, before)


class Health(Base):
    def setUp(self):
        super().setUp()
        add_default_site(self.env)

    def test_ok(self):
        r = call(tools.wp_health_check, {"site": "wp-example"})
        self.assertTrue(r["success"], r)
        self.assertEqual(r["verdict"], "ok")
        self.assertEqual(r["signals"]["home"]["status"], 200)
        self.assertTrue(r["signals"]["rest"]["ok"])
        self.assertTrue(r["signals"]["connector"]["ok"])
        self.assertEqual(call(tools.wp_sites)["sites"][0]["health"]["verdict"], "ok")

    def test_unreachable_home_is_down(self):
        self.t.responses["GET /"] = []  # falls through to 404 JSON → not ok
        self.t.base = "https://nowhere.invalid"  # every request now unreachable
        r = call(tools.wp_health_check, {"site": "wp-example"})
        self.assertEqual(r["verdict"], "down")
        self.assertTrue(any("unreachable" in f for f in r["failing"]))


class Content(Base):
    def setUp(self):
        super().setUp()
        add_default_site(self.env)

    def test_list(self):
        r = call(tools.wp_content_list, {"site": "wp-example", "type": "post", "search": "hello"})
        self.assertTrue(r["success"], r)
        self.assertEqual([i["id"] for i in r["items"]], [12, 15])
        q = self.t.requests_to("/wp/v2/posts")[0].url
        self.assertIn("context=edit", q)
        self.assertIn("search=hello", q)

    def test_get_blocks(self):
        r = call(tools.wp_content_get, {"site": "wp-example", "type": "post", "id": 12})
        self.assertTrue(r["success"], r)
        self.assertEqual(r["editor"], "blocks")
        self.assertIn("<!-- wp:paragraph -->", r["content"])
        self.assertEqual(r["title"], "Hello world!")

    def test_get_elementor_warns(self):
        r = call(tools.wp_content_get, {"site": "wp-example", "type": "page", "id": 7})
        self.assertEqual(r["page_builder"], "Elementor")
        self.assertIn("wp-admin", r["warning"])

    def test_create_defaults_to_draft(self):
        r = call(tools.wp_content_save, {"site": "wp-example", "type": "post", "title": "New post",
                                         "content": "<!-- wp:paragraph --><p>Hi</p><!-- /wp:paragraph -->"})
        self.assertTrue(r["success"], r)
        self.assertTrue(r["created"])
        post = [c for c in self.t.calls if c.method == "POST"][0]
        self.assertEqual(json.loads(post.body)["status"], "draft")
        self.assertIn("not public", r["note"])
        self.assertEqual(state.read_actions()[0]["action"], "content_save")
        self.assertNotIn("content", state.read_actions()[0].get("fields_values", {}))

    def test_update_keeps_status_unless_given(self):
        r = call(tools.wp_content_save, {"site": "wp-example", "type": "post", "id": 15, "title": "Better"})
        self.assertTrue(r["success"], r)
        post = [c for c in self.t.calls if c.method == "POST"][0]
        self.assertEqual(json.loads(post.body), {"title": "Better"})
        self.assertEqual(r["previous_status"], "draft")

    def test_page_builder_body_refused(self):
        before = len([c for c in self.t.calls if c.method == "POST"])
        r = call(tools.wp_content_save, {"site": "wp-example", "type": "page", "id": 7, "content": "<p>x</p>"})
        self.assertFalse(r["success"])
        self.assertEqual(r["kind"], "page_builder")
        self.assertEqual(len([c for c in self.t.calls if c.method == "POST"]), before)
        r = call(tools.wp_content_save, {"site": "wp-example", "type": "page", "id": 7, "content": "<p>x</p>",
                                         "confirm_page_builder": True})
        self.assertTrue(r["success"], r)

    def test_page_builder_title_only_allowed(self):
        r = call(tools.wp_content_save, {"site": "wp-example", "type": "page", "id": 7, "title": "Home!"})
        self.assertTrue(r["success"], r)

    def test_not_found(self):
        r = call(tools.wp_content_get, {"site": "wp-example", "type": "post", "id": 999})
        self.assertFalse(r["success"])
        self.assertEqual(r["kind"], "not_found")

    def test_invalid_inputs_zero_network(self):
        before = self.t.network_calls
        for handler, args in (
            (tools.wp_content_get, {"site": "wp-example", "type": "post", "id": "12; DROP"}),
            (tools.wp_content_get, {"site": "wp-example", "type": "attachment", "id": 1}),
            (tools.wp_content_get, {"site": "wp-example", "type": "post", "id": -3}),
            (tools.wp_content_save, {"site": "wp-example", "type": "post", "status": "trash", "title": "x"}),
            (tools.wp_content_save, {"site": "wp-example", "type": "post", "slug": "Bad Slug!", "title": "x"}),
            (tools.wp_content_save, {"site": "wp-example", "type": "post"}),
            (tools.wp_content_save, {"site": "wp-example", "type": "post", "id": 12}),
            (tools.wp_content_list, {"site": "wp-example", "type": "post", "status": "evil"}),
            (tools.wp_content_list, {"site": "nope", "type": "post"}),
        ):
            r = call(handler, args)
            self.assertFalse(r["success"], args)
        self.assertEqual(self.t.network_calls, before)

    def test_non_dict_args(self):
        self.assertFalse(json.loads(tools.wp_sites("nope"))["success"])
        self.assertFalse(json.loads(tools.wp_content_get(["x"]))["success"])


class HandlersNeverRaise(Base):
    def test_every_handler_survives_garbage(self):
        for name, handler in tools.HANDLERS.items():
            for args in (None, {}, {"site": None}, {"site": 123, "type": None, "id": {}}, {"tags": object()}):
                out = handler(args)
                self.assertIsInstance(json.loads(out), dict, name)


if __name__ == "__main__":
    unittest.main()
