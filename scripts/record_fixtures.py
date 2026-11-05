#!/usr/bin/env python3
"""Record replayable test fixtures from a real WordPress site running the connector.

Drives wp-manager's own core (the same code the tools use) against the site and writes every
request/response pair to a fixture file in the format tests/helpers.py replays
(``{"meta": ..., "responses": {"METHOD /path?query": [{status, headers, body}, ...]}}``).

Intended for a disposable test site (WordPress Playground, see e2e/). Never point it at production:
with --apply it really installs updates, and with --content it creates and edits a draft post.

The Application Password is read from an environment variable (never a CLI argument), is never
written to the fixture (requests are not recorded; response bodies are scrubbed), and a temporary
HERMES_HOME is used so nothing touches your real Hermes data.

Example:
    export WPM_E2E_APP_PASSWORD='xxxx xxxx xxxx xxxx xxxx xxxx'
    python scripts/record_fixtures.py --url http://127.0.0.1:9400 --user admin \\
        --password-env WPM_E2E_APP_PASSWORD --apply plugin:hello-dolly/hello.php --content \\
        --out tests/fixtures/recorded.json

    # or, with the e2e Playground's credentials file:
    python scripts/record_fixtures.py --creds-file .e2e-run/creds.json --out tests/fixtures/recorded.json
"""

from __future__ import annotations

import argparse
import importlib
import importlib.util
import json
import os
import sys
import tempfile
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_core():
    spec = importlib.util.spec_from_file_location("wpm_record_core", ROOT / "core" / "__init__.py",
                                                  submodule_search_locations=[str(ROOT / "core")])
    mod = importlib.util.module_from_spec(spec)
    sys.modules["wpm_record_core"] = mod
    spec.loader.exec_module(mod)
    return {n: importlib.import_module(f"wpm_record_core.{n}") for n in
            ("client", "credentials", "paths", "redact", "registry", "service", "settings")}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url")
    ap.add_argument("--user")
    ap.add_argument("--password-env", help="name of the environment variable holding the password")
    ap.add_argument("--creds-file", help="JSON file with url, user, app_password (e.g. .e2e-run/creds.json)")
    ap.add_argument("--out", default=str(ROOT / "tests" / "fixtures" / "recorded.json"))
    ap.add_argument("--apply", action="append", default=[], metavar="TYPE:ITEM",
                    help="run an update, e.g. plugin:hello-dolly/hello.php or theme:twentytwentyone (repeatable)")
    ap.add_argument("--content", action="store_true", help="create a draft post, read it and update it")
    ap.add_argument("--allow-insecure", action="store_true")
    a = ap.parse_args()

    password = ""
    if a.creds_file:
        creds = json.loads(Path(a.creds_file).read_text())
        a.url = a.url or creds.get("url")
        a.user = a.user or creds.get("user")
        password = str(creds.get("app_password") or "")
    elif a.password_env:
        password = os.environ.get(a.password_env, "")
    if not (a.url and a.user and password):
        print("need --url, --user and a password (--password-env NAME or --creds-file)", file=sys.stderr)
        return 2

    m = load_core()
    client, redact, service = m["client"], m["redact"], m["service"]
    home = tempfile.mkdtemp(prefix="wpm-record-")
    m["paths"].set_home_override(home)
    env_name = "WPM_RECORD_APP_PASSWORD"
    m["credentials"].set_secret_getter(lambda name: password if name == env_name else None)
    redact.remember(password, a.user)

    base = m["registry"].normalize_url(a.url)
    recorded: dict[str, list] = {}

    def recording_transport(req):
        resp = client._urllib_transport(req)  # noqa: SLF001
        path = req.url[len(base):] or "/"
        parts = urllib.parse.urlsplit(path)
        q = urllib.parse.parse_qsl(parts.query)
        key = f"{req.method} {parts.path or '/'}" + ("?" + urllib.parse.urlencode(sorted(q)) if q else "")
        text = resp.body.decode("utf-8", "replace")
        try:
            body = json.loads(text)
        except ValueError:
            body = text
        body = redact.scrub_obj(body, full=False)
        headers = {k: v for k, v in resp.headers.items() if k in ("content-type", "location")}
        recorded.setdefault(key, []).append({"status": resp.status, "headers": headers, "body": body})
        return resp

    client.set_transport(recording_transport)
    steps = []

    def step(name, fn):
        try:
            out = fn()
            if isinstance(out, dict):
                if out.get("ok") is False or out.get("success") is False:
                    raise RuntimeError(f"operation reported failure: {redact.scrub_obj(out)}")
                verdict = out.get("verdict") or (out.get("health") or {}).get("verdict")
                if verdict and verdict != "ok":
                    raise RuntimeError(f"health is {verdict}, not ok")
            steps.append({"step": name, "ok": True})
            print(f"✓ {name}")
            return out
        except Exception as exc:  # keep recording what we can
            steps.append({"step": name, "ok": False, "error": redact.scrub(str(exc))})
            print(f"✗ {name}: {redact.scrub(str(exc))}", file=sys.stderr)
            return None

    step("add site", lambda: service.add_site({"url": base, "username": a.user, "id": "record",
                                               "credential": {"env": env_name},
                                               "allow_insecure": a.allow_insecure}, actor="record"))
    site = {"id": "record", "url": base, "username": a.user, "credential": {"env": env_name},
            "allow_insecure": a.allow_insecure}
    step("scan (refresh)", lambda: service.scan_site(site, refresh=True))
    step("scan", lambda: service.scan_site(site))
    step("health", lambda: service.health_check("record"))
    def verify_installed(run):
        if not run or not run.get("success") or not run.get("results"):
            raise RuntimeError("update did not return successful item results")
        wp = service.make_client(site)
        for result in run["results"]:
            if not result.get("success") or result.get("from_version") == result.get("to_version"):
                raise RuntimeError("expected a real version change, not a no-op")
            if run["type"] == "plugin":
                installed = wp.rest("GET", "/wp/v2/plugins/" + result["item"].removesuffix(".php"),
                                    {"context": "edit"})
                version = installed["version"]
                active = installed["status"] in ("active", "network-active")
            elif run["type"] == "theme":
                installed = wp.rest("GET", "/wp/v2/themes/" + result["item"], {"context": "edit"})
                version = installed["version"]
                if isinstance(version, dict):
                    version = version["raw"]
                active = installed["status"] == "active"
            else:
                raise RuntimeError("independent version verification supports plugin/theme only")
            if version != result["to_version"] or active != result.get("active_before"):
                raise RuntimeError("installed version or activation differs from update result")
        return {"ok": True}

    for spec in a.apply:
        utype, _, item = spec.partition(":")
        run = step(f"apply {spec}", lambda: service.apply_updates(
            {"site": "record", "type": utype, "items": [item] if item else "all"}, actor="record"))
        step(f"verify installed {spec}", lambda: verify_installed(run))
    if a.content:
        created = step("create draft", lambda: service.content_save(
            {"site": "record", "type": "post", "title": "wp-manager fixture draft",
             "content": "<!-- wp:paragraph -->\n<p>Recorded by wp-manager.</p>\n<!-- /wp:paragraph -->"},
            actor="record"))
        if created:
            pid = created["id"]
            step("get draft", lambda: service.content_get({"site": "record", "type": "post", "id": pid}))
            expected_content = "<!-- wp:paragraph -->\n<p>Edited by wp-manager.</p>\n<!-- /wp:paragraph -->"
            step("update draft", lambda: service.content_save(
                {"site": "record", "type": "post", "id": pid, "title": "wp-manager fixture draft (edited)",
                 "content": expected_content}, actor="record"))

            def verify_draft():
                saved = service.content_get({"site": "record", "type": "post", "id": pid})
                if (saved.get("status") != "draft" or saved.get("title") != "wp-manager fixture draft (edited)"
                        or saved.get("content") != expected_content):
                    raise RuntimeError("draft read-back did not match the edit")
                return saved

            step("verify edited draft", verify_draft)
            step("list posts", lambda: service.content_list({"site": "record", "type": "post"}))

    fixture = {
        "meta": {"base_url": base, "username": a.user, "source": "recorded", "steps": steps},
        "responses": recorded,
    }
    text = json.dumps(fixture, indent=1, sort_keys=True) + "\n"
    if password in text or password.replace(" ", "") in text:
        print("refusing to write: the password appears in the recording", file=sys.stderr)
        return 3
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(text)
    print(f"wrote {a.out} ({len(recorded)} endpoints, {sum(len(v) for v in recorded.values())} responses)")
    return 0 if all(s["ok"] for s in steps) else 1


if __name__ == "__main__":
    sys.exit(main())
