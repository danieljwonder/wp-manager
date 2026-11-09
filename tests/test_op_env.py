"""The 1Password CLI never inherits Hermes's environment (provider keys live there)."""

import os
import unittest
from types import SimpleNamespace
from unittest import mock

from helpers import Env, submodule

credentials = submodule("core.credentials")
onepassword = submodule("core.onepassword")

HOSTILE = {
    "PATH": "/usr/bin", "HOME": "/home/u", "USER": "u", "TMPDIR": "/tmp/x", "XDG_CONFIG_HOME": "/home/u/.config",
    "OP_SESSION_myacct": "session-token", "OP_SERVICE_ACCOUNT_TOKEN": "ops_token",
    "ANTHROPIC_API_KEY": "sk-ant-leak", "OPENROUTER_API_KEY": "sk-or-leak", "GITHUB_TOKEN": "ghp_leak",
    "WPM_SITE_APP_PASSWORD": "abcd efgh", "AWS_SECRET_ACCESS_KEY": "aws-leak",
}


class OpChildEnv(unittest.TestCase):
    def test_allowlist_only(self):
        env = credentials.op_child_env(HOSTILE)
        for k in ("PATH", "HOME", "USER", "TMPDIR", "XDG_CONFIG_HOME", "OP_SESSION_myacct", "OP_SERVICE_ACCOUNT_TOKEN"):
            self.assertEqual(env[k], HOSTILE[k], k)
        for k in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "GITHUB_TOKEN", "WPM_SITE_APP_PASSWORD",
                  "AWS_SECRET_ACCESS_KEY"):
            self.assertNotIn(k, env)
        self.assertNotIn("leak", repr(env))

    def test_token_from_hermes_secret_route_when_not_in_environ(self):
        with mock.patch.dict(os.environ, {"PATH": "/usr/bin", "ANTHROPIC_API_KEY": "sk-ant-leak"}, clear=True), \
                mock.patch.object(credentials, "_default_secret_getter",
                                  lambda name: "ops_from_scope" if name == "OP_SERVICE_ACCOUNT_TOKEN" else None):
            env = credentials.op_child_env()
        self.assertEqual(env["OP_SERVICE_ACCOUNT_TOKEN"], "ops_from_scope")
        self.assertNotIn("ANTHROPIC_API_KEY", env)


class CallSitesPassScrubbedEnv(unittest.TestCase):
    def setUp(self):
        self.env = Env().start()
        self.seen = []

    def tearDown(self):
        self.env.stop()

    def fake_run(self, argv, **kw):
        self.seen.append(kw.get("env"))
        return SimpleNamespace(returncode=0, stdout="[]", stderr="")

    def test_op_read_and_item_calls(self):
        with mock.patch.dict(os.environ, HOSTILE, clear=True), \
                mock.patch.object(credentials.os.path, "isfile", return_value=True), \
                mock.patch.object(credentials.subprocess, "run", side_effect=self.fake_run), \
                mock.patch.object(onepassword.subprocess, "run", side_effect=self.fake_run):
            credentials._default_op_runner("/trusted/op", "op://v/i/password")
            onepassword._run("/trusted/op", ["vault", "list", "--format=json"])
        self.assertEqual(len(self.seen), 2)
        for env in self.seen:
            self.assertIsInstance(env, dict)
            self.assertNotIn("ANTHROPIC_API_KEY", env)
            self.assertNotIn("GITHUB_TOKEN", env)
            self.assertNotIn("WPM_SITE_APP_PASSWORD", env)
            self.assertEqual(env["OP_SERVICE_ACCOUNT_TOKEN"], "ops_token")


if __name__ == "__main__":
    unittest.main()
