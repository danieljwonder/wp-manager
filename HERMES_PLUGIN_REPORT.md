# Hermes plugin (wp-manager): build report

Scope: everything at the repo root except `connector/` and `e2e/`. Nothing was committed, pushed,
installed or enabled in the live Hermes.

## What is done

| Area | Files |
|---|---|
| Manifest | `plugin.yaml`: manifest v2, 9 tools + `pre_tool_call`, `config_schema` with `require_approval`, `op_binary`, `request_timeout`, `max_parallel`, `requires_hermes >=0.22.5` |
| Registration | `__init__.py`: `register()` only. Tools go in toolset `wp_manager`, plus the hook and the bundled skill via `ctx.register_skill`, and the cron probe via `ctx.current_cron_execution` |
| Tools | `schemas.py`, `tools.py`: handlers return JSON strings and never raise (tested with garbage input) |
| Approval | `approval.py`: approves `wp_update_apply` (`wp-manager:update:<site>`); approves `wp_content_save` when it publishes, or edits/unpublishes a published or scheduled item (`wp-manager:publish:<site>`; it looks up the live status and fails closed if the lookup fails); blocks in cron and webhook/api_server sessions. All other tools are exempt. Invalid args are not gated: the handler rejects them with zero network calls |
| Core | `core/registry.py` (sites.json, no secrets, 0600), `credentials.py`, `client.py`, `connector.py` (tolerant normalizers), `health.py`, `state.py` (state.json + actions.jsonl), `service.py` (shared by tools and API), `redact.py`, `paths.py` (`hermes_constants.get_hermes_home()` resolved per call), `settings.py` |
| Dashboard backend | `dashboard/manifest.json` + `dashboard/plugin_api.py`: every route in the spec plus `GET /sites/{id}` and `POST /connector-package`. Loads `core` by path, or reuses the agent-side copy if it is already imported. Plain `def` handlers, so work runs off the event loop. Emits `broadcast_plugin_event('wp-manager','sites.updated')` |
| Desktop | `desktop/plugin.js`: route `/wp-manager`, sidebar "WordPress" (`globe`), ⌘K "WordPress: Scan all sites" and "WordPress: Open sites". Site table (cards on narrow viewports), scan all, tag filter, search, a detail dialog (checkbox updates, Update selected/Update all through `ConfirmDialog`, health and Check now, recent actions, Open wp-admin, Remove), an Add site dialog (connection string, or url+user+env/op/store-in-.env, http warning with opt-in, shows the storage used) and a Get connector download |
| Skill | `skills/wp-manager/SKILL.md`: connection flow, backups-first, one type per update call, loud alerts, block markup, preserving structure, page-builder refusal, drafts by default |
| Docs | `README.md` (end users), `LICENSE` (MIT; notes that the connector is GPLv2+) |
| Fixtures | `scripts/record_fixtures.py` (records through core; supports `--creds-file .e2e-run/creds.json`; refuses to write if the password appears) |

### Design decisions beyond the spec (please review)

1. **Credential-binding gate.** `wp_site_add` asks for approval (`wp-manager:credential:<site>`) when it
   points a new URL at an existing secret: an `op://` reference, or a `WPM_*` name other than the
   site's own default. Otherwise a prompt-injected agent could send one site's password to an
   attacker's URL. It stays on even when `require_approval: false`. Env refs are restricted to
   `^WPM_[A-Z0-9_]+_APP_PASSWORD$`, so `OPENAI_API_KEY` and similar names are refused.
2. **Redirects.** The transport never follows redirects. Authenticated requests follow only
   same-origin redirects and never https→http. A cross-host redirect gives a clear "re-add with the
   new address" error, and no credentials are sent to the other host.
3. **`.env` fallback** uses Hermes's own `hermes_cli.config.save_env_value` (`require_env_writable`
   first). It does this only when Hermes's `get_env_path()` is the same file as the resolved home's
   `.env`; otherwise it uses a built-in atomic writer that replaces one key. Either way it then
   tightens the file to 0600 if it was group/other-readable. Note: on Umbrel/Docker Hermes keeps a
   0640 mode on purpose; the spec says 0600, so this tightens it.
4. **Health verdict.** Down = home unreachable, home 5xx, critical-error/fatal/DB-error text, or
   connector DB down. Degraded = any other failing signal, including a missing credential (the
   connector could not be checked). `wp_update_apply` sets `success: false` and adds a loud `alert`
   unless the verdict is exactly `ok`. Multi-batch Desktop updates stop at the first alert.
5. **Page builders.** `wp_content_save` refuses body edits on Elementor/Divi/WPBakery/Beaver/Oxygen/Bricks
   content unless `confirm_page_builder: true` is passed. The skill says to use that only when the
   user insists.
6. `dashboard/manifest.json` adds `"tab": {"hidden": true}` so the web dashboard does not get a
   broken tab (there is no web bundle).
7. Live updates use `ctx.onEvent('plugin.wp-manager.sites.updated')` with React Query
   `refetchInterval` (60 s list, 30 s detail) as the polling fallback. `ctx.socket` is not used.

## Tests

`cd tests && /opt/hermes/.venv/bin/python -m unittest` → **Ran 117 tests, OK** (last run on this
tree).

| Module | Tests | Covers |
|---|---|---|
| test_tools | 36 | every tool: add (env/op/connection string), duplicates, connector missing, bad auth, scan, apply (slug→file, all, all+exclude, degraded/down health ⇒ not success, 409 lock ⇒ health still checked), content (draft default, status preserved on update, page-builder refusal, 404), zero network on invalid input, handlers never raise |
| test_approval | 20 | rule keys, per-site scope, publish/edit-published/unpublish gating, draft not gated, status-lookup failure fails closed, credential binding, exempt tools, cron (probe + env) and webhook block, `require_approval: false` |
| test_privacy | 18 | full workflow: no password, Basic token or `wpmh1:` in outputs, logs, state, audit log or files (`.env` excepted); sites that echo the secret in errors or content get redacted; connection-string errors don't echo; URL userinfo and Authorization scrubbing; cross-host redirect sends no credentials; https→http refused; http refused without network unless `allow_insecure`; localhost allowed; userinfo URLs rejected; non-`WPM_` env refused |
| test_core | 29 | URL normalisation, http rules, ids/env names, sites.json 0600 with no secrets, `.env` writer (one key, others kept, 0600, refuses other keys, targets resolved home), `op read` as argv with no shell and a 15 s timeout, scrubbed stderr, connection-string decode, settings clamping, normalizers incl. **real connector samples** from the e2e Playground run, `?rest_route=` fallback, PHP-notice-before-JSON recovery |
| test_plugin_api | 6 | FastAPI TestClient over the real router: add → scan → detail → update (actor `desktop`) → actions → health → delete, password field, scrubbed JSON errors with 400/404/502, connector zip / base64 / package path, shares core with the agent copy, standalone import in a fresh `-I` interpreter |
| test_desktop | 7 | only the 3 allowed specifiers, no JSX, no hardcoded colours, every component identifier imported or defined, Hermes `desktop_surface_findings` clean, `node --check`, and a Node harness that renders 14 screen states against strict SDK stubs (StatusDot tones, Button variants/sizes, Badge variants, Checkbox `onCheckedChange`, ConfirmDialog contract, `refetchInterval ≥ 30 s`) and runs the palette scan command. The harness was mutation-checked: a dropped `StatusDot` import and a bad tone both fail it |
| test_recorded | 1 | replays `tests/fixtures/recorded.json`, a **real** read-only recording from the e2e Playground: add, scan, health |

Fixtures: `tests/fixtures/site.json` is **hand-written** to the connector's shapes (marked
`"source": "synthetic"`). `tests/fixtures/recorded.json` and `tests/fixtures/connector-samples/*`
are **real** responses: the first recorded by me (read-only) against the running Playground, the
samples copied from the other worker's `.e2e-run/samples/`. I grepped them for credentials and
found none.

## Validation

On a clean copy of the repo (what git would ship: `.research/`, `.git`, `.e2e-run` excluded):

```
hermes plugins validate . --install-deps
✓ manifest / manifest fields / requires_hermes / config schema / requires_env / oauth
✓ loadable — entry: __init__.py, desktop/plugin.js
✓ python dependencies — none declared
✓ capability probe — register() ran in isolation
✓ declared tools / hooks / middleware — matches registrations
✓ built-in tool collisions — no collisions
✓ security scan — safe            (no cautions; this includes connector/ PHP)
✓ no core override
✓ desktop surface — stays inside the plugin SDK surface
◆ Isolation — runs in the plugin host · dashboard backend API is served through the plugin host (buffered responses)
Validation passed.

hermes plugins doctor . --ci
  OK: runtime discovery, manifest parsing, import, and registration passed
  registrations: 9 tool(s), 1 hook(s)
```

Running `validate` **in the repo directory itself fails** the security scan. Every finding is in
`.research/` (the uncommitted Hermes docs copy: delegation.md, cron.md, catalog yaml…). It is
gitignored and not shipped, so it is not a plugin problem. Validate a clean checkout or temporarily
move `.research/` out.

Live check: a read-only tool run against the e2e Playground (add via connection string → scan with
refresh → health → content list/get) all succeeded. Health came back `ok`, editor `blocks`, and the
password did not appear in any output or file.

## Incident (resolved)

During that live run, my first attempt pinned a temp home, but Hermes's `save_env_value` wrote
`WPM_PLAYGROUND_APP_PASSWORD` (the disposable Playground's app password) into the **live
`/opt/data/.env`**. I removed it at once with `hermes_cli.config.remove_env_value`. `/opt/data/.env`
now has 0 `WPM_*` lines and mode 600; nothing else changed. Root cause: Hermes's writer resolves its
own path. Fixed in item 3 above, with a regression test.

## Known gaps and risks

- **Desktop page has not run in a real Hermes Desktop.** Component props were checked against
  the upstream `apps/desktop` source on GitHub `main` (StatusDot `tone`, ConfirmDialog
  `open/onClose/onConfirm`, Radix Checkbox, Button/Badge variants), not against the Desktop build
  the user has. `captureGatewayFileDownload` is feature-detected through `import * as sdk`, so a
  missing export cannot break loading. The other named imports are all in the v0.22.5 docs' UI
  kit list.
- **`plugins.isolation: host`**: plugin-host API calls with no answer after 60 s get HTTP 504 (per
  docs). A Desktop "Update" or "Scan all" that runs longer would show an error even though the
  work continues. In-process (the default) is unaffected. A job/poll pattern would fix it in
  a later version.
- **Catalog rule 11 tension**: the spec-mandated `.env` fallback writer and the direct `op read`
  ref type go beyond "credentials only through Hermes routes". The `.env` path goes through
  Hermes's own `save_env_value` where possible. A catalog reviewer may still question both. The
  default path (`get_secret`) is fully compliant.
- **Hermes secrets resolve at startup**: after `hermes secrets onepassword set WPM_…`, Hermes must
  be restarted before the site can be added (documented in README).
- The approval hook makes one authenticated GET (15 s timeout) for `wp_content_save` with an `id`,
  to learn the live status.
- I did not run updates or create posts through the tools against the Playground, to avoid
  disturbing the other worker's e2e run. Update and content result shapes are covered by the
  real connector samples plus the synthetic fixtures. A full write e2e (`record_fixtures.py
  --apply plugin:hello-dolly/hello.php --content`) is ready to run on a fresh Playground.
- `docs/connector-api.md` did not exist while I worked. Parsing follows `includes/class-service.php`
  and the real samples, and tolerates variants.
- The web dashboard (not Desktop) has no UI. Its manifest hides the tab, but the host may still
  try to fetch the default `dist/index.js` entry (unverified).
