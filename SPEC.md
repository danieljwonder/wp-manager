# wp-manager — spec v0.1

Manage a fleet of WordPress sites from Hermes: chat tools, a Hermes Desktop page,
and an on-site connector plugin. Goal: replace InfiniteWP for the day-to-day
(plugin/theme/core updates) plus create/edit posts and pages through chat.
Not a full IWP clone: no backups, no client reports, no staging, no malware scan.

Repo: `github.com/danieljwonder/wp-manager` (private until v0.1 works, then public).
Audience: Daniel first, but built so non-technical Hermes users can adopt it.
License: MIT for the Hermes plugin; GPLv2-or-later for the WordPress connector (required by WordPress.org).

## Components

1. **Hermes plugin `wp-manager`** (repo root): Python tools, hooks, skill, dashboard backend, desktop page.
2. **WordPress connector** at `connector/website-manager-hermes-connector/`:
   display name **"Website Manager: WP Connector for Hermes"**, slug/folder/text-domain
   `website-manager-hermes-connector`. A standard plugin (NOT an mu-plugin), and ready for WordPress.org submission.
   The name must not *start* with "WP" or "WordPress" (WordPress.org trademark rule).

## Repo layout

```
plugin.yaml              manifest_version 2, provides_tools/hooks exactly matching registrations
__init__.py              register() only
schemas.py               tool JSON schemas
tools.py                 handlers (return JSON strings, never raise)
core/                    pure logic + transport, shared by tools.py and dashboard/plugin_api.py
  registry.py            sites.json CRUD (no secrets)
  credentials.py         secret resolution
  client.py              HTTP client (stdlib urllib only), swappable transport for tests
  health.py              post-update health checks
  state.py               cached status per site (state.json), audit log (actions.jsonl)
dashboard/manifest.json  {"name":"wp-manager","api":"plugin_api.py"}
dashboard/plugin_api.py  FastAPI APIRouter `router` → /api/plugins/wp-manager/*
desktop/plugin.js        Desktop page (plain ESM, jsx() calls, no build step)
skills/wp-manager/SKILL.md   bundled usage skill (registered via ctx.register_skill)
connector/website-manager-hermes-connector/   WordPress plugin (PHP)
tests/                   unittest (no pytest), fixtures recorded from real WP
e2e/                     WordPress Playground scripts for real end-to-end runs
scripts/record_fixtures.py
README.md, LICENSE, .gitignore
```

Python: stdlib only. No third-party deps declared.

## Data and credentials

- `$HERMES_HOME/wp-manager/sites.json`: id (slug), label, url, username, tags[], credential ref, created_at.
  **Never contains a password.** Resolve the home with Hermes's helper (`get_hermes_home()`), never a hardcoded `~/.hermes`.
- The credential is a WordPress **Application Password** (core feature), never the admin login password.
- Credential ref is one of:
  - `{"env": "WPM_<SITE_ID>_APP_PASSWORD"}` (default). Resolved with `agent.secret_scope.get_secret(name)`.
    This covers `.env` **and** every Hermes secret source: 1Password (`hermes secrets onepassword set NAME op://...`),
    Bitwarden, and the command helper. This is the native path; document it first.
  - `{"op": "op://Vault/Item/field"}`: direct `op read` via a configurable binary (`op_binary` config, default `op`).
    Run it as a subprocess with an argument list, never a shell. Timeout 15s. Never log the value.
- Fallback for users without a vault: the Desktop "Add site" form may accept the app password and write
  `WPM_<SITE_ID>_APP_PASSWORD=...` into `$HERMES_HOME/.env` (chmod 600, append/replace that one key only,
  only keys matching `^WPM_[A-Z0-9_]+_APP_PASSWORD$`). The API never returns secrets.
- Secrets must never appear in tool output, logs, state.json, the audit log, or error messages
  (scrub `Authorization` headers and any URL userinfo).
- Refuse plain `http://` sites unless the site has `allow_insecure: true` (UI and tool both warn). Localhost/127.0.0.1 is allowed for testing.

## Connector (WordPress side)

Requirements: WP 6.5+ and PHP 7.4+. Tested up to the current WordPress release.

Transport: its own REST namespace `website-manager-hermes/v1`, authenticated by core Application Passwords.
When the Abilities API exists (WP 6.9+), also register the same operations as abilities
(`website-manager-hermes/get-status`, `/list-updates`, `/run-updates`), so the WordPress MCP Adapter and other
AI clients can use them. The REST routes and abilities both call the same internal service class.

Routes (all require an authenticated user with the right capability, all return JSON):
- `GET  /status` → connector version, WP version, PHP version, multisite flag, site/home URL, environment type,
  WP_DEBUG flags, active theme, counts of available updates, last update check time. Needs `update_plugins` or `manage_options`.
- `GET  /updates?refresh=1` → available core/plugin/theme/translation updates (name, slug/file, current version,
  new version, requires WP/PHP compatibility, auto-update enabled). `refresh=1` forces `wp_update_plugins()` /
  `wp_update_themes()` / `wp_version_check()`.
- `POST /updates` body `{type: "plugin"|"theme"|"core"|"translations", items: [...]}`, where items are plugin files
  (`akismet/akismet.php`), theme stylesheets, or empty for all of that type → runs core upgraders
  (`Plugin_Upgrader::bulk_upgrade`, `Theme_Upgrader::bulk_upgrade`, `Core_Upgrader` minor/major,
  `Language_Pack_Upgrader`) with a quiet skin. Capture the output buffer. Return per-item
  `{item, from_version, to_version, success, error}`. Keep previously active plugins active (the bulk upgrader handles
  maintenance mode and reactivation; verify this). Capability checks: `update_plugins` / `update_themes` / `update_core`.
  Guard with a transient lock so two runs can't overlap. Increase the time limit when allowed.
- `GET  /health` → a cheap self-report: recovery mode active?, `error_get_last()` from this request, DB reachable,
  WP cron health, paused plugins/themes (from `wp_paused_plugins()` / `wp_paused_themes()`).
- `GET  /log` → last 50 connector actions (option, capped, autoload off): time, user, action, items, result.

Admin UI: **Tools → Hermes Connector**
- Connection status and requirements check (HTTPS, application passwords available, REST reachable).
- Button: "Create connection for Hermes". Creates an application password named "Hermes (website-manager)"
  for the current user via `WP_Application_Passwords::create_new_application_password`, nonce + capability checked,
  and shows a **one-time connection string**: base64 JSON `{v:1, url, user, app_password}`, prefixed `wpmh1:`,
  with a copy button. Explain that it's shown once and can be revoked under Users → Profile → Application Passwords.
- Recent actions log.
- Optional allowlist toggles: allow core updates (default on), allow major core updates (default off).

Standards: WordPress Coding Standards; prefix everything (`wmhc_`, `WMHC_` / class namespace `WebsiteManagerHermesConnector`).
Do not prefix with `wp_`. Escape output and sanitize input. Use nonces and capability checks everywhere. No remote calls except
WordPress's own update APIs. No tracking, no phoning home. `uninstall.php` removes options. Include `readme.txt` (WordPress.org format),
GPLv2+ header, `Requires at least`, `Requires PHP`, `Tested up to`, and a translation-ready text domain.

## Hermes tools (toolset `wp_manager`)

All handlers take `(args, **kwargs)`, return a JSON string, and never raise.

- `wp_sites` → list registered sites with cached status (versions, pending update counts, last check, health).
- `wp_site_add` {url, username, label?, tags?, credential?: {env|op}, connection_string?} → verifies
  `/status`, then saves. If a `connection_string` is given it is decoded and **must not** be echoed back; the
  password goes to the fallback `.env` writer only when no env/op ref is given, and the result states where it went.
  Prefer guiding users to paste connection strings into the Desktop form rather than chat.
- `wp_site_remove` {site}.
- `wp_updates_scan` {sites?: [ids] | tag?, refresh?: bool} → concurrent (thread pool, max 4) status+updates fetch; updates state.json.
- `wp_update_apply` {site, type, items?: [..] | "all", exclude?: [..]} → POST /updates, then runs a health check, then rescans.
  Returns a per-item result plus health verdict. **Approval-gated.**
- `wp_health_check` {site} → GET home URL (status code, "critical error" string detection, response time),
  GET /wp-json/ reachable, connector `/health`. Verdict: ok | degraded | down.
- `wp_content_list` {site, type: post|page, search?, status?, per_page?}
- `wp_content_get` {site, type, id} → title, status, slug, content.raw (context=edit), excerpt, link, modified.
- `wp_content_save` {site, type, id?, title?, content?, excerpt?, status?: draft|pending|publish|private, slug?}
  → create or update via core `/wp/v2/posts|pages`. **Default status is draft.** Publishing, or editing an
  already-published item, is approval-gated.
- (Later, not v0.1: media upload, categories/tags, plugin activate/deactivate, scheduled scans.)

Content: accept HTML/block markup. The bundled skill tells the agent to write Gutenberg block markup for block sites,
and to fetch and preserve existing content structure when editing (Elementor/ACF-built pages: warn and refuse body edits
unless the user insists, because raw `content` isn't what renders there).

## Approval and safety

- A `pre_tool_call` hook returns `{"action":"approve", ...}` for `wp_update_apply` and for gated `wp_content_save`
  calls, with a narrow `rule_key` such as `wp-manager:update:<site>` or `wp-manager:publish:<site>`. It fails closed
  in unattended contexts (cron). Exempt every other tool.
- Config `config_schema`: `require_approval` (bool, default true), `op_binary` (str, default "op"),
  `request_timeout` (int, default 60; updates use max(300, timeout)), `max_parallel` (int, default 4).
- The Desktop "Update" button counts as explicit human approval (direct backend call, not an agent tool).
- Every write action is appended to `$HERMES_HOME/wp-manager/actions.jsonl` (no secrets).
- After any update: run the health check. If degraded/down, say so loudly in the result, with the failing signal.
  Never report success on a failed or unverified health check.
- README states plainly: no backups. Take or confirm host backups before bulk updates.

## Desktop page

`desktop/plugin.js`: route `/wp-manager` + sidebar nav row "WordPress" (codicon e.g. `globe`) + ⌘K command "WordPress: Scan all sites".
- Site table (cards on narrow widths): label, URL, tags, WP version, PHP version, connector version,
  pending plugin/theme/core/translation updates, health dot (`StatusDot`), last checked.
- Toolbar: Scan all, filter by tag, search.
- Site detail drawer/dialog: lists pending updates with checkboxes, "Update selected" / "Update all" (`ConfirmDialog`),
  recent actions, health result, "Open wp-admin" link.
- "Add site" dialog: paste connection string OR url+username+credential ref (env name / op:// ref / "store in .env" password field).
  Show which storage was used.
- Data via React Query (`useQuery` with `ctx.rest`), sensible `refetchInterval` (≥30s), no hand-rolled polling.
  `ctx.socket` is optional and must have a polling fallback.
- Use only SDK UI components and theme CSS variables. No hardcoded colours. No JSX syntax. Import only from
  `@hermes/plugin-sdk`, `react`, `react/jsx-runtime`.
- Backend routes (`dashboard/plugin_api.py`, exporting `router = APIRouter()`):
  `GET /sites`, `POST /sites`, `DELETE /sites/{id}`, `POST /scan`, `POST /sites/{id}/scan`,
  `POST /sites/{id}/update`, `GET /sites/{id}/health`, `GET /actions`, `GET /connector.zip` (zip of the bundled connector
  for easy install). They reuse `core/`. Import `core` robustly from the plugin dir (plugin_api is loaded by the dashboard host, so
  don't rely on package-relative imports; add the plugin root to `sys.path` carefully, or load it via importlib).

Remote-desktop note: when Hermes Desktop connects to a remote Hermes (e.g. Umbrel), the backend half runs remotely and
`desktop/plugin.js` must be present in the Desktop machine's `$HERMES_HOME/desktop-plugins/wp-manager/`. Document this.

## Reference docs (local copies)

`.research/hermes-agent-main/website/docs/` (not committed):
- `developer-guide/plugins/index.md`, `developer-guide/plugins/catalog-submission.md`
- `developer-guide/desktop-plugin-sdk.md` (backend: "A backend for your plugin" section)
- `user-guide/features/extending-the-dashboard.md`
- `user-guide/secrets/*.md`
- `.research/hermes-agent-main/agent/secret_scope.py`
- `.research/hermes-agent-main/plugin-catalog/README.md` (admission rules)
Existing plugin by the same author to mirror for style/layout/tests: `/opt/data/plugins/agentic-bitcoin-explorer/`.
Installed Hermes: v0.22.5 at `/opt/hermes` (venv `/opt/hermes/.venv`) → `requires_hermes: ">=0.22.5"`.

## Testing

- Unit tests: `cd tests && /opt/hermes/.venv/bin/python -m unittest`. Swappable transport with recorded fixtures.
  Assert privacy: secrets never appear in any tool output/log/state; invalid input makes zero network calls.
- E2E with **WordPress Playground CLI** (`npx @wp-playground/cli@latest server`, works here; no Docker/PHP on host):
  mount the connector, `WP_ENVIRONMENT_TYPE=local` (Application Passwords need HTTPS or a local environment),
  install an **older** version of a real wordpress.org plugin (e.g. a pinned old version of `classic-editor` or `hello-dolly`)
  and an older theme, create an app password via blueprint `runPHP`, then drive the Hermes `core/` + tools against it:
  add site → scan (sees update) → apply → version changed → health ok → create draft post → update it.
  Record fixtures from this run with `scripts/record_fixtures.py`.
- PHP lint the connector inside Playground (or `php -l` via the wasm runtime) if feasible.
- Hermes validation: `/opt/hermes/.venv/bin/hermes plugins validate . --install-deps` and `hermes plugins doctor . --ci`.
  Fix every security-scan caution.

## Gates (do not do without Daniel's explicit go-ahead)

- Installing or enabling the plugin in the live Hermes (`/opt/data/plugins`, `plugins.enabled`).
- Making the repo public, catalog PR, WordPress.org submission.
- Touching any real production WordPress site.
Commits: local commits plus pushes to the private repo are fine. Git identity must be
`danieljwonder <2772782+danieljwonder@users.noreply.github.com>`.
