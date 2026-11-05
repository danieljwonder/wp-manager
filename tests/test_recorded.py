"""Replay a fixture recorded from a real WordPress site (scripts/record_fixtures.py), if present."""

import json
import unittest

from helpers import ROOT, SECRET, Env, call, submodule

RECORDED = ROOT / "tests" / "fixtures" / "recorded.json"


class Transport:
    def __init__(self, fixture):
        self.base = fixture["meta"]["base_url"].rstrip("/")
        self.responses = {k: list(v) for k, v in fixture["responses"].items()}
        self.calls = []
        self.inner = None

    def __call__(self, req):
        import urllib.parse

        client = submodule("core.client")
        self.calls.append(req)
        path = req.url[len(self.base):] or "/"
        parts = urllib.parse.urlsplit(path)
        q = urllib.parse.parse_qsl(parts.query)
        key = f"{req.method} {parts.path or '/'}" + ("?" + urllib.parse.urlencode(sorted(q)) if q else "")
        seq = self.responses.get(key)
        if not seq:
            raise client.WPError(f"no recording for {key}", kind="network")
        rec = seq.pop(0) if len(seq) > 1 else seq[0]
        body = rec["body"]
        raw = body.encode() if isinstance(body, str) else json.dumps(body).encode()
        return client.Response(rec["status"], dict(rec.get("headers") or {}), raw)


@unittest.skipUnless(RECORDED.exists(), "no recorded fixture (run scripts/record_fixtures.py)")
class Recorded(unittest.TestCase):
    def setUp(self):
        self.fixture = json.loads(RECORDED.read_text())
        self.env = Env(transport=Transport(self.fixture)).start()

    def tearDown(self):
        self.env.stop()

    def test_add_scan_health_replay(self):
        tools = submodule("tools")
        meta = self.fixture["meta"]
        insecure = meta["base_url"].startswith("http://")
        self.env.secrets["WPM_RECORD_APP_PASSWORD"] = SECRET
        r = call(tools.wp_site_add, {"url": meta["base_url"], "username": meta["username"], "id": "record",
                                     "credential": {"env": "WPM_RECORD_APP_PASSWORD"}, "allow_insecure": insecure})
        self.assertTrue(r["success"], r)
        self.assertTrue(r["verified"]["wp_version"])
        r = call(tools.wp_updates_scan, {})
        self.assertTrue(r["sites"][0]["ok"], r)
        r = call(tools.wp_health_check, {"site": "record"})
        self.assertIn(r["verdict"], ("ok", "degraded", "down"))


if __name__ == "__main__":
    unittest.main()
