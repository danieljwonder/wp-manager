"""Static and smoke checks for desktop/plugin.js (no browser, no React needed).

* only the three allowed import specifiers, no JSX syntax, no hardcoded colours;
* every capitalised identifier used as a component is imported or defined in the file;
* ``node --check`` parses it; a Node harness renders every screen against strict SDK stubs;
* Hermes's own desktop-surface admission lint passes (when Hermes is importable).
"""

import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from helpers import ROOT

JS = ROOT / "desktop" / "plugin.js"
SOURCE = JS.read_text()
NODE = shutil.which("node")
ALLOWED = {"@hermes/plugin-sdk", "react", "react/jsx-runtime"}


def strip_comments(src):
    src = re.sub(r"/\*.*?\*/", "", src, flags=re.S)
    return re.sub(r"(?m)^\s*//.*$", "", src)


class Static(unittest.TestCase):
    def test_only_allowed_imports(self):
        specs = set(re.findall(r"""\bfrom\s+['"]([^'"]+)['"]""", SOURCE))
        specs |= set(re.findall(r"""\bimport\s+['"]([^'"]+)['"]""", SOURCE))
        self.assertTrue(specs)
        self.assertLessEqual(specs, ALLOWED)
        self.assertNotRegex(SOURCE, r"\bimport\s*\(")

    def test_no_jsx_syntax(self):
        code = strip_comments(SOURCE)
        code = re.sub(r"'(?:[^'\\\n]|\\.)*'|`(?:[^`\\]|\\.)*`", "''", code)
        self.assertNotRegex(code, r"<[A-Za-z][\w.]*(\s[^<>]*)?/?>")

    def test_no_hardcoded_colours(self):
        code = strip_comments(SOURCE)
        self.assertNotRegex(code, r"#[0-9a-fA-F]{3,8}\b")
        self.assertNotRegex(code, r"\b(rgb|rgba|hsl|hsla|oklch)\(")
        self.assertNotRegex(code, r"""['"](black|white|red|green|blue|gray|grey)['"]""")

    def test_every_component_identifier_is_imported_or_defined(self):
        imported = set()
        for block in re.findall(r"import\s*\{([^}]*)\}\s*from", SOURCE):
            imported |= {n.strip().split(" as ")[-1] for n in block.split(",") if n.strip()}
        defined = set(re.findall(r"\bfunction\s+([A-Z]\w*)", SOURCE))
        defined |= set(re.findall(r"\bconst\s+([A-Z]\w*)\s*=", SOURCE))
        used = set(re.findall(r"\b(?:h|jsx|jsxs)\(\s*([A-Z]\w*)", strip_comments(SOURCE)))
        missing = used - imported - defined
        self.assertEqual(missing, set())
        for name in ("StatusDot", "ConfirmDialog", "useQuery"):
            self.assertIn(name, imported)

    def test_hermes_desktop_surface_lint(self):
        try:
            from hermes_cli.plugin_validate_desktop import desktop_surface_findings
        except ImportError:
            self.skipTest("Hermes not importable")
        self.assertEqual(desktop_surface_findings(SOURCE), [])


@unittest.skipUnless(NODE, "node not installed")
class Node(unittest.TestCase):
    def test_node_check_parses(self):
        out = subprocess.run([NODE, "--check", str(JS)], capture_output=True, text=True, timeout=60)
        self.assertEqual(out.returncode, 0, out.stderr)

    def test_renders_every_screen_against_stubs(self):
        self.run_harness("harness.mjs")

    def test_initial_detail_loading_lifecycle(self):
        self.run_harness("initial-detail.mjs")

    def run_harness(self, name):
        tmp = Path(tempfile.mkdtemp(prefix="wpm-desktop-"))
        try:
            stubs = (ROOT / "tests" / "desktop" / "stubs.mjs").resolve().as_uri()
            code = SOURCE
            for spec in ALLOWED:
                code = re.sub(r"""(['"])""" + re.escape(spec) + r"""\1""", repr(stubs).replace("'", "'"), code)
            target = tmp / "plugin.mjs"
            target.write_text(code)
            harness = ROOT / "tests" / "desktop" / name
            out = subprocess.run([NODE, str(harness), target.as_uri()], capture_output=True, text=True, timeout=60)
            self.assertEqual(out.returncode, 0, out.stdout + out.stderr)
            self.assertRegex(out.stdout, r"OK \d+ renders")
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
