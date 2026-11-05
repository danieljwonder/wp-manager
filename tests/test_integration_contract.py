"""Joined-contract regressions using recordings from disposable WordPress."""
import json
import unittest
from pathlib import Path
from helpers import submodule

connector = submodule("core.connector")


class IntegrationContract(unittest.TestCase):
    def test_operation_failure_overrides_successful_items(self):
        item = {"item": "a/a.php", "success": True}
        for extra in ({"success": False}, {"result": "partial"}, {"errors": ["global failure"]}):
            with self.subTest(extra=extra):
                result = connector.normalize_run({"success": True, "result": "success",
                                                  "results": [item], **extra})
                self.assertFalse(result["all_succeeded"])

    def test_real_recording_has_verified_writes(self):
        data = json.loads((Path(__file__).parent / "fixtures" / "recorded.json").read_text())
        steps = {s["step"]: s["ok"] for s in data["meta"]["steps"]}
        self.assertTrue(all(steps.values()))
        for name in ("verify installed plugin:classic-editor/classic-editor.php",
                     "verify installed theme:twentytwentyfour", "verify edited draft"):
            self.assertTrue(steps[name])
        runs = data["responses"]["POST /wp-json/website-manager-hermes/v1/updates"]
        self.assertEqual(len(runs), 2)
        for response in runs:
            run = connector.normalize_run(response["body"])
            self.assertTrue(run["all_succeeded"])
            for item in run["results"]:
                self.assertNotEqual(item["from_version"], item["to_version"])
                self.assertTrue(item["active_after"])
        gets = [r["body"] for k, values in data["responses"].items()
                if k.startswith("GET /wp-json/wp/v2/posts/") for r in values]
        self.assertTrue(any(p.get("status") == "draft"
                            and p.get("title", {}).get("raw") == "wp-manager fixture draft (edited)"
                            and "Edited by wp-manager." in p.get("content", {}).get("raw", "")
                            for p in gets))
