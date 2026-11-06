# Connector implementation and real Playground results

Historical run: **2026-11-05 UTC**, connector version **0.1.0**, against disposable WordPress Playground sites. These are recorded results, not a new run or a claim about current upstream versions. Redacted samples remain checked in below.

## Outcome

The standard WordPress plugin **Website Manager: WP Connector for Hermes** is implemented at `connector/website-manager-hermes-connector/`. All five REST routes, per-type update operations, Abilities registrations, Tools admin page, one-time connection flow, settings, capped non-autoloaded log, and uninstall cleanup are present.

The harness booted real WordPress in the official Playground CLI, with the connector mounted and old WordPress.org plugin/theme packages installed. No update responses or packages were mocked. Core's own HTTP/update APIs downloaded and installed the actual updates.

### Verified runs

| Run | Runtime actually reported by `/status` | Result |
|---|---|---|
| Main | WordPress **7.1.4**, PHP **8.3.36**, local environment | **48/48** checks passed |
| Minimum supported branch | WordPress **6.5.14**, PHP **7.4.34-dev**, local environment | **44/44** checks passed |
| Major core update, on the minimum-version site | WordPress **6.5.14 → 7.1.4** | **10/10** checks passed |
| PHP-wasm lint | PHP 8.3.36 and 7.4.34-dev | 10/10 PHP files parse/compile and `php -l` clean on both |

Actual extension changes:

* Classic Editor **1.6.3 → 1.7.0**, active before **and** after. Independently confirmed through core `/wp/v2/plugins` (not just connector output). Fallback reactivation was not needed (`reactivated:null`).
* Hello Dolly **1.6 → 1.7.2**, inactive before **and** after.
* Twenty Twenty-Four **1.0 → 1.6**, still the active theme. Independently confirmed through core `/wp/v2/themes?status=active` and `/status`.
* A real `plugin:hello-dolly:de_DE` translation update downloaded `https://downloads.wordpress.org/translation/plugin/hello-dolly/1.7.2/de_DE.zip` and installed it successfully in a separate manual curl run. The returned target extension version was 1.7.2. This is not a fabricated language-pack test; the recorded response is below. A translation offer is not deterministic on every fresh Playground boot because installing a language/update can already install its packs.
* Core **6.5.14 → 7.1.4** downloaded the real `wordpress-7.1.4-new-bundled.zip`, installed it using `Core_Upgrader`, returned the on-disk new version, and left `db_upgrade_required:false`. `/status`, `/health`, and the homepage all passed afterwards.

### Behavior tested

* Basic auth with a real admin Application Password created by blueprint `runPHP`.
* `GET /status`, `GET /updates?refresh=1`, `POST /updates` (plugins, themes, core, translations), `GET /health`, `GET /log`.
* Fresh WordPress.org API data: both plugin and theme update offers appeared with real versions/requirements. `refresh_errors:[]`.
* A concurrent update request was blocked with **409 `wmhc_locked`**, while a plugin update was in progress.
* A mixed plugin run returned `result:partial`, `success:false`, while its two real installed items succeeded and an unknown file reported `not_installed`. Item order preserved.
* Unauthenticated and wrong-password requests returned **401**. Invalid type returned **400**. An unoffered core target returned **404**.
* Major core updates refused by default (**403 `wmhc_major_core_updates_disabled`**). Allowed after toggling the real admin settings form (nonce-protected `options.php` POST). An automatic selection when only a blocked major offer exists returns an honest `skipped` result, not `up_to_date`.
* `GET /wp-json/wp-abilities/v1/abilities?per_page=100` listed all three `website-manager-hermes/*` abilities on 7.1.4. Each execute endpoint worked. Anonymous execution was rejected. On WP 6.5 the Abilities route was absent and the connector continued to work.
* Cookie login to `/wp-admin`, Tools → Hermes Connector page rendered with the menu link and recent actions. Bad nonce was rejected with 403. Good nonce created a `wpmh1:` connection string. It decoded to v/url/user/app_password (plus optional rest_url), and its new Application Password authenticated `/status`. A subsequent GET did not show the secret again. `connection.create` was logged without a password.
* Healthy `/health`: `status:ok`, database `SELECT 1` reachable, recovery/maintenance false, no paused extensions, no request-local error.
* Update transients cleared by `Language_Pack_Upgrader` are repopulated; the automated verifier checks last-check timestamps remain populated after translations.

## Harness and implementation details

Run commands are in [`README.md`](README.md). `start-playground.sh` launches `npx -y @wp-playground/cli@latest server`, mounts only the connector and `.e2e-run`, writes a generated blueprint with explicit `preferredVersions`, and waits for credentials plus REST readiness. `stop-playground.sh` kills the entire process group. `.e2e-run` is gitignored and private (0700; umask 077). No password is in checked-in samples.

The API contract is precisely documented in [`../docs/connector-api.md`](../docs/connector-api.md). Samples in `e2e/samples/` retain exact JSON response values; the `http_status`/`request`/`body` wrapper is recording metadata, not the wire envelope.

The connector uses core Application Password authentication (no custom auth protocol), REST permission callbacks, service-level write capability checks, admin nonces, sanitized identifiers and escaped admin output. Shared internal `Service` is called by REST and Abilities. The transient lock marker is backed by core's **atomic option-backed** `WP_Upgrader::create_lock()` rather than relying on a racy transient read/set. Update lock TTL is 900 seconds. A quiet `Automatic_Upgrader_Skin` subclass captures messages/errors. Outer output buffering is unwound safely, including if a nested skin buffer was left open by an exception.

The log stores the latest 50 actions in `wmhc_log` with autoload off; no secret-bearing connection data is logged. Uninstall deletes `wmhc_log`, `wmhc_settings`, the lock and marker (including multisite blog options). User Application Passwords remain deliberately revocable in Profile; uninstall does not silently remove a user's credential.

The plugin has GPLv2-or-later metadata, text domain matching its slug, WordPress.org-format `readme.txt`, minimum WP/PHP headers and a non-`WP`-prefixed display name. No external service, tracking, telemetry, or remote call outside WordPress's own update mechanisms was introduced.

## Problems encountered and honest limitations

1. **CLI blueprint version override.** Passing `--php 7.4 --wp 6.5` while supplying a blueprint without `preferredVersions` printed those flags but actually served PHP 8.5.12 / WordPress 7.1.4. This was caught by checking `/status`, not trusting CLI output. The start script now injects `preferredVersions:{php,wp}`; the final runs really reported 8.3.36/7.1.4 and 7.4.34-dev/6.5.14. A branch selector `6.5` resolves to the latest patch 6.5.14, not exact 6.5.0.
2. **Playground multi-worker `.maintenance` race.** On both runtimes, one of the CLI's six workers briefly retained a stale view after removal of `.maintenance`. A probe returned **HTTP 200 with HTML warning/fatal**, not valid JSON: `require(/wordpress/.maintenance): Failed to open stream`, followed by `Failed opening required '/wordpress/.maintenance'`. This occurs in WordPress bootstrap before the connector runs. The verifier logs it explicitly and performs 12 clean health probes to let workers converge, then makes the substantive assertions. It does not turn that HTML into a fake successful JSON response. The final runs each observed one such transient after the plugin run, and all subsequent health/core version checks passed. Production clients must treat non-JSON/unhealthy responses as failures; the waiting accommodation belongs only to this Playground harness.
3. **Raw PHP probe certificates.** A standalone PHP-wasm probe using raw curl/file streams failed with CA certificate-location/verification errors. This did **not** block WordPress: core's HTTP transport successfully checked WordPress.org and downloaded all real plugin/theme/core/language packages. No TLS-verification bypass was added to the connector.
4. **Translation cache clearing.** A manual language-pack run exposed that core clears all update transients; without repopulation a following scan could misleadingly show zero counts. Fixed in the service and covered by timestamp checks in the final verifier.
5. **No formal PHPCS/WPCS scan.** No native PHP is installed. Syntax was genuinely checked with PHP-wasm (`TOKEN_PARSE`, compile pass and actual `php -l`) under both supported runtimes. The code follows WordPress conventions and escaping/sanitization patterns, but this report does not claim a successful PHPCS certification.
6. **Not exercised:** multisite behavior/uninstall loop, an actual minor-core package upgrade (major-core path was exercised), failing package-download rollback/reactivation fallback, paused extension/recovery mode branches, the copy button in a graphical browser, and the external WordPress MCP Adapter itself. Its discoverability metadata is registered (`show_in_rest`, `public`, `mcp.public`), and the core Abilities list/execution are verified; adapter interoperability still needs its own test.
7. These runs cover the connector and harness only. Joined Python/connector evidence and current test instructions are documented in [testing](../docs/testing.md); they have a separate scope.

## Exact recorded REST response samples

The following are actual recordings. All passwords are redacted. Full files are linked for use as client fixtures.

### GET /status

[`status.json`](samples/status.json)

```json
{
  "http_status": 200,
  "body": {
    "connector_version": "0.1.0",
    "wp_version": "7.1.4",
    "php_version": "8.3.36",
    "multisite": false,
    "site_url": "http://127.0.0.1:9400",
    "home_url": "http://127.0.0.1:9400",
    "rest_url": "http://127.0.0.1:9400/wp-json/",
    "site_name": "WMHC E2E",
    "environment_type": "local",
    "debug": {
      "wp_debug": true,
      "wp_debug_log": true,
      "wp_debug_display": false,
      "script_debug": false
    },
    "active_theme": {
      "name": "Twenty Twenty-Four",
      "stylesheet": "twentytwentyfour",
      "template": "twentytwentyfour",
      "version": "1.0"
    },
    "updates": {
      "core": 0,
      "plugins": 0,
      "themes": 0,
      "translations": 0,
      "total": 0
    },
    "last_checked": {
      "core": null,
      "plugins": null,
      "themes": null
    },
    "file_mods_allowed": true,
    "filesystem_method": "direct",
    "abilities_api": true,
    "application_passwords": true,
    "settings": {
      "allow_core_updates": true,
      "allow_major_core_updates": false
    },
    "user": {
      "id": 1,
      "login": "admin",
      "capabilities": {
        "update_plugins": true,
        "update_themes": true,
        "update_core": true,
        "update_languages": true,
        "manage_options": true
      }
    },
    "generated_at": "2026-11-05T23:07:55+00:00"
  }
}
```

### GET /updates?refresh=1

[`updates-refresh.json`](samples/updates-refresh.json)

```json
{
  "http_status": 200,
  "body": {
    "refreshed": true,
    "refresh_errors": [],
    "last_checked": {
      "core": "2026-11-05T23:07:56+00:00",
      "plugins": "2026-11-05T23:07:57+00:00",
      "themes": "2026-11-05T23:07:58+00:00"
    },
    "counts": {
      "core": 0,
      "plugins": 2,
      "themes": 1,
      "translations": 0,
      "total": 3
    },
    "core": [],
    "plugins": [
      {
        "type": "plugin",
        "name": "Classic Editor",
        "file": "classic-editor/classic-editor.php",
        "slug": "classic-editor",
        "current_version": "1.6.3",
        "new_version": "1.7.0",
        "requires_wp": "4.9",
        "requires_php": "5.2.4",
        "tested_wp": "7.0.8",
        "compatible_wp": true,
        "compatible_php": true,
        "active": true,
        "network_active": false,
        "auto_update_enabled": false,
        "package_available": true
      },
      {
        "type": "plugin",
        "name": "Hello Dolly",
        "file": "hello-dolly/hello.php",
        "slug": "hello-dolly",
        "current_version": "1.6",
        "new_version": "1.7.2",
        "requires_wp": "4.6",
        "requires_php": null,
        "tested_wp": "6.9.11",
        "compatible_wp": true,
        "compatible_php": true,
        "active": false,
        "network_active": false,
        "auto_update_enabled": false,
        "package_available": true
      }
    ],
    "themes": [
      {
        "type": "theme",
        "name": "Twenty Twenty-Four",
        "stylesheet": "twentytwentyfour",
        "slug": "twentytwentyfour",
        "current_version": "1.0",
        "new_version": "1.6",
        "requires_wp": "6.4",
        "requires_php": "7.0",
        "compatible_wp": true,
        "compatible_php": true,
        "active": true,
        "auto_update_enabled": false,
        "package_available": true
      }
    ],
    "translations": []
  }
}
```

### POST /updates (plugins, partial result)

[`updates-post-plugin.json`](samples/updates-post-plugin.json)

```json
{
  "http_status": 200,
  "request": {
    "type": "plugin",
    "items": [
      "classic-editor/classic-editor.php",
      "hello-dolly/hello.php",
      "not-installed/nope.php"
    ]
  },
  "body": {
    "type": "plugin",
    "success": false,
    "result": "partial",
    "started_at": "2026-11-05T23:07:59+00:00",
    "duration_ms": 4843,
    "results": [
      {
        "item": "classic-editor/classic-editor.php",
        "name": "Classic Editor",
        "from_version": "1.6.3",
        "to_version": "1.7.0",
        "success": true,
        "status": "updated",
        "error": null,
        "active_before": true,
        "active_after": true,
        "reactivated": null,
        "messages": [
          "Downloading update from https://downloads.wordpress.org/plugin/classic-editor.1.7.0.zip…",
          "Unpacking the update…",
          "Installing the latest version…",
          "Removing the old version of the plugin…",
          "Plugin updated successfully."
        ]
      },
      {
        "item": "hello-dolly/hello.php",
        "name": "Hello Dolly",
        "from_version": "1.6",
        "to_version": "1.7.2",
        "success": true,
        "status": "updated",
        "error": null,
        "active_before": false,
        "active_after": false,
        "reactivated": null,
        "messages": [
          "Downloading update from https://downloads.wordpress.org/plugin/hello-dolly.1.7.2.zip…",
          "Unpacking the update…",
          "Installing the latest version…",
          "Removing the old version of the plugin…",
          "Plugin updated successfully."
        ]
      },
      {
        "item": "not-installed/nope.php",
        "name": null,
        "from_version": null,
        "to_version": null,
        "success": false,
        "status": "not_installed",
        "error": "Plugin is not installed.",
        "active_before": null,
        "active_after": null,
        "reactivated": null,
        "messages": []
      }
    ],
    "errors": [],
    "messages": [
      "Enabling Maintenance mode…",
      "Disabling Maintenance mode…"
    ],
    "output": "",
    "maintenance_off": true
  }
}
```

### POST /updates (themes, empty items = all)

[`updates-post-theme.json`](samples/updates-post-theme.json)

```json
{
  "http_status": 200,
  "request": {
    "type": "theme",
    "items": []
  },
  "body": {
    "type": "theme",
    "success": true,
    "result": "success",
    "started_at": "2026-11-05T23:08:19+00:00",
    "duration_ms": 4972,
    "results": [
      {
        "item": "twentytwentyfour",
        "name": "Twenty Twenty-Four",
        "from_version": "1.0",
        "to_version": "1.6",
        "success": true,
        "status": "updated",
        "error": null,
        "active_before": true,
        "active_after": true,
        "messages": [
          "Downloading update from https://downloads.wordpress.org/theme/twentytwentyfour.1.6.zip…",
          "Unpacking the update…",
          "Installing the latest version…",
          "Removing the old version of the theme…",
          "Theme updated successfully."
        ]
      }
    ],
    "errors": [],
    "messages": [
      "Enabling Maintenance mode…",
      "Disabling Maintenance mode…"
    ],
    "output": "",
    "maintenance_off": true
  }
}
```

### POST /updates (concurrent lock refusal)

[`updates-post-locked.json`](samples/updates-post-locked.json)

```json
{
  "http_status": 409,
  "body": {
    "code": "wmhc_locked",
    "message": "Another update run is in progress. Try again in a few minutes.",
    "data": {
      "status": 409
    }
  }
}
```

### POST /updates (latest core, no-op)

[`updates-post-core-uptodate.json`](samples/updates-post-core-uptodate.json)

```json
{
  "http_status": 200,
  "body": {
    "type": "core",
    "success": true,
    "result": "success",
    "started_at": "2026-11-05T23:08:36+00:00",
    "duration_ms": 8,
    "results": [
      {
        "item": "core",
        "name": "WordPress",
        "from_version": "7.1.4",
        "to_version": "7.1.4",
        "success": true,
        "status": "up_to_date",
        "error": null,
        "db_upgrade_required": false,
        "messages": []
      }
    ],
    "errors": [],
    "messages": [],
    "output": "",
    "maintenance_off": true
  }
}
```

### GET /updates (core offer on WP 6.5.14)

[`core-updates-core-offer.json`](samples/core-updates-core-offer.json)

```json
{
  "http_status": 200,
  "body": {
    "core": [
      {
        "type": "core",
        "name": "WordPress",
        "current_version": "6.5.14",
        "new_version": "7.1.4",
        "locale": "en_US",
        "is_major": true,
        "requires_php": "7.4",
        "requires_mysql": "5.5.5",
        "compatible_php": true,
        "allowed": false,
        "auto_update_enabled": true,
        "dismissed": false,
        "package_available": true
      }
    ],
    "counts": {
      "core": 1,
      "plugins": 1,
      "themes": 0,
      "translations": 0,
      "total": 2
    }
  }
}
```

### POST /updates (blocked major core target)

[`core-error-major-core-disabled.json`](samples/core-error-major-core-disabled.json)

```json
{
  "http_status": 403,
  "body": {
    "code": "wmhc_major_core_updates_disabled",
    "message": "Major WordPress updates through Hermes are disabled in Tools → Hermes Connector.",
    "data": {
      "status": 403
    }
  }
}
```

### POST /updates (real core update)

[`core-updates-post-core.json`](samples/core-updates-post-core.json)

```json
{
  "http_status": 200,
  "body": {
    "type": "core",
    "success": true,
    "result": "success",
    "started_at": "2026-11-05T23:11:06+00:00",
    "duration_ms": 22479,
    "results": [
      {
        "item": "core",
        "name": "WordPress",
        "from_version": "6.5.14",
        "to_version": "7.1.4",
        "success": true,
        "status": "updated",
        "error": null,
        "db_upgrade_required": false,
        "messages": [
          "Downloading update from https://downloads.wordpress.org/release/wordpress-7.1.4-new-bundled.zip…",
          "Unpacking the update…"
        ]
      }
    ],
    "errors": [],
    "messages": [],
    "output": "",
    "maintenance_off": true
  }
}
```

### POST /updates (real German translation update)

[`translations-updates-post.json`](samples/translations-updates-post.json)

```json
{
  "http_status": 200,
  "request": {
    "type": "translations",
    "items": [
      "plugin:hello-dolly:de_DE"
    ]
  },
  "body": {
    "type": "translations",
    "success": true,
    "result": "success",
    "started_at": "2026-11-05T23:02:43+00:00",
    "duration_ms": 1012,
    "results": [
      {
        "item": "plugin:hello-dolly:de_DE",
        "name": "hello-dolly",
        "from_version": null,
        "to_version": "1.7.2",
        "success": true,
        "status": "updated",
        "error": null,
        "language": "de_DE",
        "updated": "2026-03-17 21:06:31",
        "messages": [
          "Herunterladen der Übersetzung von https://downloads.wordpress.org/translation/plugin/hello-dolly/1.7.2/de_DE.zip …",
          "Entpacken der aktualisierten Version …",
          "Die neueste Version wird installiert …",
          "Alte Übersetzung wird entfernt …",
          "Die Übersetzung wurde erfolgreich aktualisiert."
        ]
      }
    ],
    "errors": [],
    "messages": [],
    "output": "",
    "maintenance_off": true
  }
}
```

### GET /health

[`health.json`](samples/health.json)

```json
{
  "http_status": 200,
  "body": {
    "status": "ok",
    "ok": true,
    "problems": [],
    "warnings": [],
    "recovery_mode": false,
    "maintenance_mode": false,
    "last_error": null,
    "database": {
      "reachable": true,
      "error": null
    },
    "cron": {
      "disabled": false,
      "alternate": false,
      "scheduled_events": 8,
      "overdue_events": 0,
      "overdue_threshold_s": 3600,
      "oldest_overdue_at": null,
      "next_event_at": "2026-11-06T00:07:38+00:00",
      "doing_cron": false
    },
    "paused_plugins": [],
    "paused_themes": [],
    "wp_version": "7.1.4",
    "connector_version": "0.1.0",
    "checked_at": "2026-11-05T23:08:49+00:00"
  }
}
```

### GET /log

[`log.json`](samples/log.json)

```json
{
  "http_status": 200,
  "body": {
    "entries": [
      {
        "time": "2026-11-05T23:08:37+00:00",
        "timestamp": 1793920117,
        "user_id": 1,
        "user_login": "admin",
        "action": "updates.run",
        "items": [],
        "result": "success",
        "type": "translations",
        "source": "rest",
        "summary": []
      },
      {
        "time": "2026-11-05T23:08:37+00:00",
        "timestamp": 1793920117,
        "user_id": 1,
        "user_login": "admin",
        "action": "updates.run",
        "items": [
          "1.0"
        ],
        "result": "error",
        "type": "core",
        "source": "rest",
        "error": "That WordPress version is not offered as an update."
      },
      {
        "time": "2026-11-05T23:08:36+00:00",
        "timestamp": 1793920116,
        "user_id": 1,
        "user_login": "admin",
        "action": "updates.run",
        "items": [
          "core"
        ],
        "result": "success",
        "type": "core",
        "source": "rest",
        "summary": [
          {
            "item": "core",
            "from_version": "7.1.4",
            "to_version": "7.1.4",
            "success": true
          }
        ]
      },
      {
        "time": "2026-11-05T23:08:24+00:00",
        "timestamp": 1793920104,
        "user_id": 1,
        "user_login": "admin",
        "action": "updates.run",
        "items": [
          "twentytwentyfour"
        ],
        "result": "success",
        "type": "theme",
        "source": "rest",
        "summary": [
          {
            "item": "twentytwentyfour",
            "from_version": "1.0",
            "to_version": "1.6",
            "success": true
          }
        ]
      },
      {
        "time": "2026-11-05T23:08:04+00:00",
        "timestamp": 1793920084,
        "user_id": 1,
        "user_login": "admin",
        "action": "updates.run",
        "items": [
          "classic-editor/classic-editor.php",
          "hello-dolly/hello.php",
          "not-installed/nope.php"
        ],
        "result": "partial",
        "type": "plugin",
        "source": "rest",
        "summary": [
          {
            "item": "classic-editor/classic-editor.php",
            "from_version": "1.6.3",
            "to_version": "1.7.0",
            "success": true
          },
          {
            "item": "hello-dolly/hello.php",
            "from_version": "1.6",
            "to_version": "1.7.2",
            "success": true
          },
          {
            "item": "not-installed/nope.php",
            "from_version": null,
            "to_version": null,
            "success": false
          }
        ]
      }
    ],
    "count": 5,
    "max": 50
  }
}
```

### GET /status without authentication

[`error-unauthenticated.json`](samples/error-unauthenticated.json)

```json
{
  "http_status": 401,
  "body": {
    "code": "rest_forbidden",
    "message": "Sorry, you are not allowed to do that.",
    "data": {
      "status": 401
    }
  }
}
```

### POST /updates invalid type

[`error-invalid-type.json`](samples/error-invalid-type.json)

```json
{
  "http_status": 400,
  "body": {
    "code": "rest_invalid_param",
    "message": "Invalid parameter(s): type",
    "data": {
      "status": 400,
      "params": {
        "type": "type is not one of plugin, theme, core, and translations."
      },
      "details": {
        "type": {
          "code": "rest_not_in_enum",
          "message": "type is not one of plugin, theme, core, and translations.",
          "data": null
        }
      }
    }
  }
}
```

### One-time connection decoded (password redacted)

[`connection-string-decoded.json`](samples/connection-string-decoded.json)

```json
{
  "prefix": "wpmh1:",
  "decoded": {
    "v": 1,
    "url": "http://127.0.0.1:9400",
    "user": "admin",
    "app_password": "[REDACTED]",
    "rest_url": "http://127.0.0.1:9400/wp-json/"
  }
}
```

### Abilities discovery

The three registered names from the real list response:

* `website-manager-hermes/get-status`
* `website-manager-hermes/list-updates`
* `website-manager-hermes/run-updates`

Full core list entries, schemas, annotations and execute links: [`abilities-list.json`](samples/abilities-list.json). Actual list/run execution response recordings: [`ability-list-updates.json`](samples/ability-list-updates.json), [`ability-run-updates.json`](samples/ability-run-updates.json).
