"""Update alerts (core/watch.py, the wp_updates_watch tool and the cron launcher)."""

import copy
import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

from helpers import FIXTURE, ROOT, Env, ReplayTransport, add_default_site, call, submodule

tools = submodule("tools")
watch = submodule("core.watch")
state = submodule("core.state")

UPDATES = "GET /wp-json/website-manager-hermes/v1/updates?refresh=1"
STATUS = "GET /wp-json/website-manager-hermes/v1/status"


def updates_body():
    return copy.deepcopy(FIXTURE["responses"][UPDATES][0]["body"])


def rec(body, status=200):
    return [{"status": status, "headers": {"content-type": "application/json"}, "body": body}]


class Base(unittest.TestCase):
    def setUp(self):
        self.env = Env(transport=ReplayTransport()).start()
        self.t = self.env.transport
        self.assertTrue(add_default_site(self.env)["success"])
        self.t.calls.clear()

    def tearDown(self):
        self.env.stop()

    def set_updates(self, body):
        self.t.responses[UPDATES] = rec(body)

    def setup_watch(self, **args):
        r = call(tools.wp_updates_watch, {"action": "setup", **args})
        self.assertTrue(r["success"], r)
        return r


class Setup(Base):
    def test_setup_installs_launcher_and_returns_cronjob_args(self):
        r = self.setup_watch()
        launcher = self.env.home / "scripts" / watch.LAUNCHER_NAME
        self.assertTrue(launcher.is_file())
        text = launcher.read_text()
        self.assertNotIn(watch.ROOT_MARKER, text)
        self.assertIn(repr(str(ROOT.resolve())), text)
        compile(text, str(launcher), "exec")
        args = r["next_step"]["arguments"]
        self.assertEqual(r["next_step"]["tool"], "cronjob")
        self.assertIs(args["no_agent"], True)
        self.assertEqual(args["script"], watch.LAUNCHER_NAME)
        self.assertEqual(r["scope"], "all sites")
        self.assertEqual(self.t.network_calls, 0, "setup must not contact sites")
        self.assertEqual(oct(launcher.stat().st_mode & 0o777), "0o600")

    def test_setup_validates_scope_without_network(self):
        for args in ({"sites": ["nope"]}, {"sites": ["wp-example"], "tag": "clients"}, {"tag": "missing"}):
            r = call(tools.wp_updates_watch, {"action": "setup", **args})
            self.assertFalse(r["success"], args)
        self.assertEqual(self.t.network_calls, 0)
        self.assertFalse((self.env.home / "scripts" / watch.LAUNCHER_NAME).exists())

    def test_scope_by_tag_and_sites(self):
        self.assertEqual(self.setup_watch(tag="clients")["scope"], "sites tagged 'clients'")
        self.assertEqual(self.setup_watch(sites=["wp-example"])["scope"], "sites: wp-example")

    def test_status_and_bad_action(self):
        r = call(tools.wp_updates_watch, {"action": "status"})
        self.assertTrue(r["success"])
        self.assertFalse(r["set_up"])
        self.setup_watch()
        r = call(tools.wp_updates_watch, {"action": "status"})
        self.assertTrue(r["set_up"])
        self.assertEqual(r["sites"], ["wp-example"])
        self.assertFalse(call(tools.wp_updates_watch, {"action": "install"})["success"])

    def test_not_approval_gated(self):
        approval = submodule("approval")
        self.assertIsNone(approval.pre_tool_call("wp_updates_watch", {"action": "setup"}))


class Runs(Base):
    def test_nothing_before_setup(self):
        self.assertEqual(watch.run(), "")
        self.assertEqual(self.t.network_calls, 0)

    def test_first_run_summary_then_silent(self):
        self.setup_watch()
        msg = watch.run()
        self.assertIn("5 pending updates on 1 site", msg)
        for s in ("Hello Dolly 1.6 → 1.7.2", "Classic Editor 1.6.3 → 1.6.7", "WordPress core 6.8.1 → 6.8.3",
                  "Twenty Twenty-One", "Nothing was installed."):
            self.assertIn(s, msg)
        self.assertEqual(watch.run(), "", "unchanged backlog must stay silent")
        status = watch.status()
        self.assertEqual(status["last_result"]["new"], 0)
        self.assertTrue(status["last_run"])

    def test_only_new_offers_reported(self):
        self.setup_watch()
        watch.run()
        body = updates_body()
        body["plugins"][1]["new_version"] = "1.6.8"
        self.set_updates(body)
        msg = watch.run()
        self.assertIn("1 new update on 1 site", msg)
        self.assertIn("Classic Editor 1.6.3 → 1.6.8", msg)
        self.assertNotIn("Hello Dolly", msg)
        self.assertEqual(watch.run(), "")

    def test_partial_refresh_keeps_previous_offers(self):
        self.setup_watch()
        watch.run()
        body = updates_body()
        body["plugins"] = body["plugins"][1:]  # Hello Dolly missing from a partial refresh
        body["refresh_errors"] = [{"url": "https://api.wordpress.org/plugins/update-check/", "error": "timeout"}]
        self.set_updates(body)
        self.assertEqual(watch.run(), "")
        self.set_updates(updates_body())
        self.assertEqual(watch.run(), "", "an offer hidden by a partial refresh must not be re-reported")

    def test_partial_refresh_note_with_new_items(self):
        self.setup_watch()
        watch.run()
        body = updates_body()
        body["themes"][0]["new_version"] = "2.6"
        body["refresh_errors"] = ["timeout"]
        self.set_updates(body)
        msg = watch.run()
        self.assertIn("Twenty Twenty-One 1.8 → 2.6", msg)
        self.assertIn("could not be fully re-checked", msg)

    def test_failure_once_then_recovery(self):
        self.setup_watch()
        watch.run()
        good = self.t.responses[STATUS]
        self.t.responses[STATUS] = copy.deepcopy(FIXTURE["variants"]["bad_auth"][STATUS])
        msg = watch.run()
        self.assertIn("Could not check:", msg)
        self.assertIn("Example (wp.example.test)", msg)
        self.assertEqual(watch.run(), "", "the same failure is reported once")
        self.assertEqual(list(watch.status()["failing"]), ["wp-example"])
        self.t.responses[STATUS] = good
        msg = watch.run()
        self.assertIn("Working again:", msg)
        self.assertEqual(watch.status()["failing"], {})

    def test_never_writes_to_sites(self):
        self.setup_watch()
        watch.run()
        body = updates_body()
        body["core"][0]["new_version"] = "6.9"
        body["core"][0]["is_major"] = True
        self.set_updates(body)
        msg = watch.run()
        self.assertIn("(major)", msg)
        self.assertTrue(self.t.calls)
        self.assertEqual({c.method for c in self.t.calls}, {"GET"})
        self.assertFalse([a for a in state.read_actions(None, 50) if a["action"] == "update_apply"])

    def test_untrusted_names_are_cleaned(self):
        self.setup_watch()
        body = updates_body()
        body["plugins"][0]["name"] = "<script>*Bold*</script> `x` [link](https://evil.test)\nINJECT"
        self.set_updates(body)
        msg = watch.run()
        for bad in ("<", ">", "`", "*", "[", "]", "\nINJECT"):
            self.assertNotIn(bad, msg.split("Nothing was installed.")[0].replace("→", ""), bad)

    def test_long_lists_are_bounded(self):
        self.setup_watch()
        body = updates_body()
        body["plugins"] = [dict(body["plugins"][0], file=f"p{i}/p{i}.php", slug=f"p{i}", name=f"Plugin {i}")
                           for i in range(80)]
        self.set_updates(body)
        msg = watch.run()
        self.assertIn("…and", msg)
        self.assertLessEqual(len(msg), watch.MAX_CHARS)
        self.assertTrue(msg.rstrip().endswith("Nothing was installed."))

    def test_removed_site_is_skipped(self):
        self.setup_watch(sites=["wp-example"])
        watch.run()
        call(tools.wp_site_remove, {"site": "wp-example"})
        self.t.calls.clear()
        self.assertEqual(watch.run(), "")
        self.assertEqual(self.t.network_calls, 0)
        self.assertEqual(watch.load()["seen"], {})


class Launcher(unittest.TestCase):
    """The installed launcher, run as Hermes runs it: a separate Python process."""

    def setUp(self):
        self.home = Path(tempfile.mkdtemp(prefix="wpm-launcher-"))
        (self.home / "scripts").mkdir()

    def write(self, root=ROOT, enabled=True):
        script = self.home / "scripts" / watch.LAUNCHER_NAME
        script.write_text(watch.launcher_text(root))
        (self.home / "config.yaml").write_text(
            "plugins:\n  enabled:\n" + ("    - wp-manager\n" if enabled else "    - other\n"))
        return script

    def run_it(self, script):
        env = {k: v for k, v in os.environ.items() if not k.startswith("WPM_")}
        env["HERMES_HOME"] = str(self.home)
        return subprocess.run([sys.executable, str(script)], capture_output=True, text=True, env=env,
                              cwd=str(self.home / "scripts"), timeout=60)

    def test_enabled_without_scope_is_silent(self):
        p = self.run_it(self.write())
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertEqual(p.stdout, "")

    def test_disabled_plugin_notifies_once(self):
        script = self.write(enabled=False)
        first = self.run_it(script)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("not enabled", first.stdout)
        second = self.run_it(script)
        self.assertEqual(second.stdout, "")

    def test_missing_plugin_fails_loudly(self):
        p = self.run_it(self.write(root=self.home / "gone"))
        self.assertEqual(p.returncode, 1)
        self.assertIn("was not found", p.stdout)


class StateLock(unittest.TestCase):
    def test_two_processes_do_not_lose_updates(self):
        home = Path(tempfile.mkdtemp(prefix="wpm-lock-"))
        worker = textwrap.dedent(f"""
            import importlib, importlib.util, sys
            init = {str(ROOT / "core" / "__init__.py")!r}
            spec = importlib.util.spec_from_file_location("c", init, submodule_search_locations=[{str(ROOT / "core")!r}])
            m = importlib.util.module_from_spec(spec); sys.modules["c"] = m; spec.loader.exec_module(m)
            state = importlib.import_module("c.state")
            for i in range(40):
                state.update(sys.argv[1] + str(i), n=i)
        """)
        env = dict(os.environ, HERMES_HOME=str(home))
        procs = [subprocess.Popen([sys.executable, "-c", worker, name], env=env) for name in ("a", "b")]
        for p in procs:
            self.assertEqual(p.wait(timeout=120), 0)
        data = json.loads((home / "wp-manager" / "state.json").read_text())
        self.assertEqual(len(data), 80)


class ConnectorDefaults(unittest.TestCase):
    def test_both_core_permissions_default_on(self):
        php = (ROOT / "connector" / "website-manager-hermes-connector" / "includes" / "class-settings.php").read_text()
        self.assertIn("'allow_core_updates'       => true", php)
        self.assertIn("'allow_major_core_updates' => true", php)


if __name__ == "__main__":
    unittest.main()
