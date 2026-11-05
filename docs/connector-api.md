# Connector API v1 (0.1.0)

Source of truth for the Python client. These shapes were exercised against real WordPress in Playground; complete recorded responses (HTTP status + body) are in [`e2e/samples/`](../e2e/samples/). No Application Password is recorded there.

## Transport and authentication

* Namespace: `website-manager-hermes/v1`.
* With pretty permalinks: `<site>/wp-json/website-manager-hermes/v1/<route>`.
* With plain permalinks: `<site>/?rest_route=/website-manager-hermes/v1/<route>`; append query args with `&`.
* All routes require an authenticated WordPress user. Use core **Application Passwords**, HTTP Basic auth (`Authorization: Basic base64(username:application_password)`) over **HTTPS**. Local HTTP works when `WP_ENVIRONMENT_TYPE=local`.
* Cookie authentication is also supported by core (send `X-WP-Nonce: <wp_rest nonce>`). Basic-auth requests do **not** need a REST nonce: core authenticates each request using the Application Password.
* Send `Accept: application/json`; send `Content-Type: application/json` for POST.
* No response envelope on the wire: the response body is the JSON object itself. The `http_status`, `request`, `body` keys in sample files are recording metadata, **not** wire fields.
* Fields shown below are present unless explicitly marked optional. `S?` means string **or null**; a nullable key is still present. Times are UTC ISO-8601 (`2026-11-05T23:07:55+00:00`) unless otherwise noted. Clients should tolerate extra fields in future minor releases.
* GET operations do not force an update check unless `refresh=1` is passed to `/updates`. A newly booted site may show zero counts with null `last_checked` values; do not interpret that as a verified fresh scan.

### Read capabilities

GET routes require at least one of `update_plugins`, `update_themes`, `update_core`, `manage_options`. Updates require the capability for their type, regardless of whether the user may read the routes. Multisite uses core's usual capability mapping (normally super admin).

## `GET /status`

HTTP 200. See [`status.json`](../e2e/samples/status.json).

| Key | Type / meaning |
|---|---|
| `connector_version`, `wp_version`, `php_version` | strings |
| `multisite` | boolean |
| `site_url`, `home_url`, `rest_url` | strings; `rest_url` has trailing slash when pretty permalinks are enabled; it may use `?rest_route=/` otherwise |
| `site_name` | string |
| `environment_type` | string: `local`, `development`, `staging`, `production` |
| `debug` | object of booleans: `wp_debug`, `wp_debug_log`, `wp_debug_display`, `script_debug` (log path deliberately not exposed) |
| `active_theme` | object: `name`, `stylesheet`, `template`, `version` strings |
| `updates` | `Counts` (below) |
| `last_checked` | `LastChecked` (below) |
| `file_mods_allowed` | boolean (core's `wp_is_file_mod_allowed`) |
| `filesystem_method` | string (usually `direct`; other methods may need stored credentials) |
| `abilities_api` | boolean (`wp_register_ability` exists) |
| `application_passwords` | boolean (site-level availability) |
| `settings` | object of booleans: `allow_core_updates` (default true), `allow_major_core_updates` (default false) |
| `user` | object: `id` integer, `login` string, `capabilities` object of booleans `update_plugins`, `update_themes`, `update_core`, `update_languages`, `manage_options` |
| `generated_at` | ISO UTC string |

`Counts` = `{core:int, plugins:int, themes:int, translations:int, total:int}` (nonnegative, total is sum).

`LastChecked` = `{core:ISO|null, plugins:ISO|null, themes:ISO|null}`. These are core's last-check timestamps, not a guarantee that the check succeeded; inspect `refresh_errors` when forcing a refresh.

## `GET /updates?refresh=1`

`refresh` is a boolean query parameter (default false; `1`/`true` accepted). A refresh forces `wp_version_check`, `wp_update_plugins`, `wp_update_themes` using WordPress's own APIs. It does **not** install anything.

HTTP 200:

```text
{
  refreshed: bool,
  refresh_errors: [{url: string, error: string}],
  last_checked: LastChecked,
  counts: Counts,
  core: CoreOffer[],
  plugins: PluginOffer[],
  themes: ThemeOffer[],
  translations: TranslationOffer[]
}
```

All four arrays always exist (empty `[]` if none). `refresh_errors` reports failed `api.wordpress.org` requests (URL has no query or credentials). A fallback request may succeed after an earlier request failed, so an error entry does not by itself mean the whole check failed. `refreshed:true` means the refresh was **attempted**, not necessarily successful.

### PluginOffer

```text
{
  type: "plugin", name: string,
  file: string, slug: string,
  current_version: S?, new_version: S?,
  requires_wp: S?, requires_php: S?, tested_wp: S?,
  compatible_wp: bool, compatible_php: bool,
  active: bool, network_active: bool,
  auto_update_enabled: bool, package_available: bool
}
```

`file` (e.g. `classic-editor/classic-editor.php`) is the identifier to send in POST `items`. `auto_update_enabled` reflects the site's plugin auto-update option and global enablement; it is advisory (other filters can affect automatic updates).

### ThemeOffer

```text
{
  type: "theme", name: string,
  stylesheet: string, slug: string,
  current_version: string, new_version: S?,
  requires_wp: S?, requires_php: S?,
  compatible_wp: bool, compatible_php: bool,
  active: bool, auto_update_enabled: bool, package_available: bool
}
```

`stylesheet` is the POST item identifier (e.g. `twentytwentyfour`). `active:true` means active stylesheet **or** active parent template.

### CoreOffer

```text
{
  type: "core", name: "WordPress",
  current_version: string, new_version: string, locale: string,
  is_major: bool, requires_php: S?, requires_mysql: S?,
  compatible_php: bool, allowed: bool, auto_update_enabled: bool,
  dismissed: bool, package_available: bool
}
```

Only `response:upgrade` core offers are listed; core's "latest" and "development" offers aren't pending updates. `is_major` means a different x.y branch (6.5 → 6.6 is major; 6.5.1 → 6.5.2 is minor). `allowed` is the connector allowlist only, **not** the user's capability or server writability. Core may offer several versions/locales. `auto_update_enabled` uses core's policy and global updater disablement.

### TranslationOffer

```text
{
  type: "translation", item: string,
  translation_type: S?, slug: string,
  language: S?, version: S?, updated: S?,
  auto_update_enabled: bool
}
```

`item` = `type:slug:language` (e.g. `plugin:hello-dolly:de_DE`), unique within a check. `updated` is WordPress.org's timestamp string (e.g. `2026-03-17 21:06:31`), **not** ISO. `version` is the extension/core version the translation targets, not a standalone translation version. For core the fallback slug is `default`.

Recorded examples: [`updates-refresh.json`](../e2e/samples/updates-refresh.json), [`core-updates-core-offer.json`](../e2e/samples/core-updates-core-offer.json), [`translations-updates-list.json`](../e2e/samples/translations-updates-list.json).

## `POST /updates`

Requires `update_plugins` (plugin), `update_themes` (theme), `update_core` (core), or `update_languages` (translations). Uses the core upgraders with a quiet skin, captures output, and releases the update lock in a `finally` block. Core's option-backed lock `wmhc_updates.lock` is atomic and expires after 900 seconds; this avoids the race of a non-atomic transient check/set. It guards **all types** on this site.

Request:

```json
{"type":"plugin","items":["classic-editor/classic-editor.php"]}
```

* `type` is required, one of `plugin`, `theme`, `core`, `translations` (note the plural).
* `items` is an optional array of strings. Omitted or `[]` = all pending updates of that type (core: highest offered version allowed by the settings).
* Plugin items are installed plugin files; theme items are installed stylesheets. Unknown installed identifiers return an item-level failure, not an HTTP error. Installed items with no update return `up_to_date`.
* Core items: use `[]` or `["<offered version>"]`. Only the **first** core item is considered. It cannot install arbitrary versions/packages, only an offer returned by core's update API.
* Translation items: `type:slug:language` IDs, or slugs (a slug selects all languages for that extension). Unknown translation identifiers currently select nothing and return an empty successful run.
* Identifiers are sanitized; duplicates/empty strings removed. Never send URLs or package paths.
* The connector has no backup feature. Check host backups first. Then run a separate health check and rescan — a successful upgrader response is **not** a site-health guarantee.

### Run response (HTTP 200 even when an individual item fails)

```text
{
  type: "plugin"|"theme"|"core"|"translations",
  success: bool,
  result: "success"|"partial"|"failed",
  started_at: ISO,
  duration_ms: int,
  results: ItemResult[],
  errors: string[],
  messages: string[],
  output: string,
  maintenance_off: bool
}
```

* `success` = true only when all returned items succeeded (or no items needed work). `partial` = some succeeded; `failed` = none succeeded.
* `errors` and `messages` are global upgrader messages not tied to an item. `output` is captured stray output (plain text, truncated to 8,000 bytes; normally empty). Messages/errors are localized; never parse their wording.
* `maintenance_off` only checks that `.maintenance` is absent at completion. Always check `/health` and the homepage afterwards.
* Update transients cleared by the upgraders are repopulated before returning.
* Do not retry an update POST merely because the HTTP connection timed out: the server may still be updating. Scan first; lock conflicts return 409.

### ItemResult (common keys, always present)

```text
{
  item: string,
  name: S?,
  from_version: S?,
  to_version: S?,
  success: bool,
  status: "updated"|"up_to_date"|"failed"|"skipped"|"not_installed",
  error: S?,
  ...type-specific keys below...,
  messages: string[]
}
```

* `error:null` on success; item-level failures have a string.
* `from_version` is read before upgrading; `to_version` is read from the installed file afterwards, not copied blindly from the offer. An unchanged version on failure is expected. For translations `from_version` is null (core does not expose the old translation version); `to_version` is the target extension version from the translation offer.
* `skipped`: earlier item stopped the bulk upgrader, or a core update was left alone because only major releases are offered and major updates are disabled. A policy-skipped core item is `success:true` with a message (no work attempted); an aborted bulk item is `success:false`.

Plugin-only keys (always present): `active_before:bool|null`, `active_after:bool|null`, `reactivated:bool|null`. Unknown plugins use nulls. `reactivated:null` means no fallback reactivation was needed; true/false means the connector tried to reactivate a previously active plugin. Core's bulk upgrader normally keeps plugins active without this fallback. Inactive plugins are not activated.

Theme-only keys: `active_before:bool|null`, `active_after:bool|null`.

Core-only key: `db_upgrade_required:bool`. Core upgrader normally runs its database-upgrade loopback; if that failed, this may be true and needs attention. `item` is `core`, `name` is `WordPress`.

Translation-only keys: `language:S?`, `updated:S?` (WordPress.org timestamp).

Full examples: [`updates-post-plugin.json`](../e2e/samples/updates-post-plugin.json) (partial), [`updates-post-theme.json`](../e2e/samples/updates-post-theme.json), [`core-updates-post-core.json`](../e2e/samples/core-updates-post-core.json) (real 6.5.14 → 7.1.4), [`translations-updates-post.json`](../e2e/samples/translations-updates-post.json).

## `GET /health`

Cheap request-local self-report; it does **not** fetch the homepage or perform WordPress Site Health's full tests. HTTP 200 even if unhealthy:

```text
{
  status: "ok"|"warning"|"degraded"|"critical",
  ok: bool,
  problems: string[], warnings: string[],
  recovery_mode: bool, maintenance_mode: bool,
  last_error: null|{type:int, fatal:bool, message:string, file:string, line:int},
  database: {reachable:bool, error:S?},
  cron: {
    disabled:bool, alternate:bool, scheduled_events:int, overdue_events:int,
    overdue_threshold_s:int, oldest_overdue_at:ISO|null, next_event_at:ISO|null,
    doing_cron:bool
  },
  paused_plugins: PausedEntry[], paused_themes: PausedEntry[],
  wp_version:string, connector_version:string, checked_at:ISO
}
```

`PausedEntry` = `{name:string, type:int|null, message:S?, file:S?, line:int|null}`. Paths in health reports are relative to WordPress root (or basename if outside it). `last_error` is `error_get_last()` from **this request**, not a history or a debug.log tail.

`status` rules:
* `critical`: database unreachable or a fatal request-local error.
* `degraded`: recovery mode, maintenance mode, paused plugins/themes (or other problem).
* `warning`: only cron events more than an hour overdue.
* `ok`: none of the above.

`ok:true` for `ok` **or** `warning`; clients should display the warning separately. `problems` codes: `database_unreachable`, `paused_plugins`, `paused_themes`, `recovery_mode`, `maintenance_mode`, `fatal_error`. `warnings`: `cron_overdue`. Cron disabled is reported, but is not itself unhealthy (host may use system cron). This probe doesn't spawn cron or perform a slow loopback test.

See [`health.json`](../e2e/samples/health.json).

## `GET /log?limit=50`

`limit`: integer 1–50, default 50. HTTP 200:

```text
{
  entries: LogEntry[],
  count: int,
  max: 50
}
```

Newest first; capped at 50 in the non-autoloaded `wmhc_log` option.

Common LogEntry keys: `time:ISO`, `timestamp:int` (Unix seconds), `user_id:int`, `user_login:string`, `action:string`, `items:string[]`, `result:string`.

Action-dependent extra keys:
* `updates.run`: `type:string`, `source:"rest"|"ability"`, and either `summary:[{item:string,from_version:S?,to_version:S?,success:bool}]` or `error:string` for an operation-level error. Results: `success|partial|failed|error`.
* `connection.create`: `source:"admin"`, items contains the Application Password **name**, never the password or connection string. Result `success`.
* `settings.save`: `source:"admin"`, items contains strings such as `allow_core_updates=on`. Result `success`.

Read-only requests, rejected authentication/capability/validation requests, and rejected lock acquisitions aren't logged. See [`log.json`](../e2e/samples/log.json).

## Errors

Operation-level errors use core REST error shape with a non-2xx status:

```json
{"code":"wmhc_locked","message":"Another update run is in progress. Try again in a few minutes.","data":{"status":409}}
```

`data` can contain additional core validation details (`params`, `details`). `message` is localized; branch on HTTP status and `code`, not the message.

| HTTP | Code | Meaning |
|---|---|---|
| 401/403 | `rest_forbidden` | Anonymous or user lacks capability (core may return a different Application Password authentication error first) |
| 400 | `rest_missing_callback_param`, `rest_invalid_param` | Missing `type`, wrong enum/type, invalid query args/items |
| 400 | `wmhc_invalid_type` | Service-level invalid type (normally REST validation catches first) |
| 401/403 | `wmhc_forbidden` | Service-level capability guard |
| 403 | `wmhc_file_mods_disallowed` | Core disallows file modifications |
| 403 | `wmhc_core_updates_disabled` | Core allowlist is off |
| 403 | `wmhc_major_core_updates_disabled` | Explicit major target requested while major allowlist is off |
| 404 | `wmhc_no_core_update` | Explicit target not offered by core |
| 409 | `wmhc_locked` | Another connector update run is active |
| 500 | `wmhc_exception` | Unexpected runtime exception |

Upgrader failures for installed items are normally HTTP **200**, with `success:false` and details in `results[].error`. HTTP status alone is not a success verdict.

## Abilities API (WP 6.9+)

These use the **same service class**, capabilities, locks, and response bodies as the REST routes. Registration is skipped harmlessly on older WordPress.

List: `GET /wp-json/wp-abilities/v1/abilities?per_page=100` (look for the three names; includes core abilities too).

| Name | Execute endpoint | Input |
|---|---|---|
| `website-manager-hermes/get-status` | `GET /wp-json/wp-abilities/v1/abilities/website-manager-hermes/get-status/run` | none |
| `website-manager-hermes/list-updates` | `GET …/list-updates/run?input[refresh]=false` | optional `{refresh:bool}` |
| `website-manager-hermes/run-updates` | `POST …/run-updates/run` | JSON `{"input":{"type":"plugin","items":["hello-dolly/hello.php"]}}` |

All category `website-manager-hermes`, `show_in_rest:true`, public/discoverable in WP 7.1+ and marked `mcp.public:true` for the WordPress MCP Adapter. **Public here means discoverable, not unauthenticated execution**; permission callbacks still guard every execution. `get-status`/`list-updates`: annotations `{readonly:true,destructive:false,idempotent:true}`. `run-updates`: `{readonly:false,destructive:true,idempotent:false}` so core uses POST. Input schemas reject additional properties. Errors are core ability errors (`ability_invalid_input`, `rest_ability_cannot_execute`, etc.).

MCP Adapter itself was not installed/tested; the core Abilities list and all three execute endpoints were verified.

## Admin connection string

Tools → Hermes Connector creates a normal Application Password for the current user, nonce + `manage_options` + `create_app_password` capability checked. The name is `Hermes (website-manager)` (a UTC timestamp suffix if one already exists).

Format: `wpmh1:` followed by **standard base64** of UTF-8 JSON:

```json
{"v":1,"url":"https://example.com","user":"admin","app_password":"[REDACTED]","rest_url":"https://example.com/wp-json/"}
```

`v`, `url`, `user`, `app_password` are required; `rest_url` is an optional extra hint for clients to support plain permalinks/subdirectory installs. The password is six space-separated four-character chunks (core accepts with or without spaces). The string is shown once in the current POST response only and is not persisted. Revoke under Users → Profile → Application Passwords. Do not log or echo the string. Uninstall removes connector options, **not** the user's Application Passwords; revoke them separately.
