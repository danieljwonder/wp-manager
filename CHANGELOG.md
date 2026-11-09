# Changelog

## 0.1.4

Security fixes from catalog review.

- `wp_site_add` from chat now always asks for approval, showing the site URL and the secret that
  will be sent to it. Previously a site using its own default `WPM_<SITE_ID>_APP_PASSWORD` skipped
  the prompt, so a colliding id, or removing a site and re-adding its id with a different URL,
  could send an existing Application Password to a model-chosen URL without a prompt.
- Refuse site ids whose default secret name collides with an existing site's (`my-blog` and
  `my--blog` both map to `WPM_MY_BLOG_APP_PASSWORD`), in Desktop and chat alike.
- `allow_insecure: true` gets its own approval rule (`wp-manager:credential:<site>:insecure`), so
  "always allow" for an https site never covers a plain-http one.
- The 1Password CLI (and any `op_binary` wrapper) now runs with a minimal allowlisted environment
  (PATH, HOME, USER, temp/XDG dirs, `OP_SESSION_*`, 1Password Connect/account variables,
  `OP_SERVICE_ACCOUNT_TOKEN`) instead of inheriting Hermes's, which holds every provider key. The
  token is read through Hermes secrets when it isn't in the environment.
- Align `core.VERSION`, `dashboard/manifest.json` and the bundled skill with the plugin version.

## 0.1.3

- Remove the `.env` storage option and the plugin's `.env` writer. wp-manager no longer reads or
  writes the Hermes `.env` file (plugin catalog rule 11). Connection strings and plain
  Application Passwords are saved only as a new 1Password item; otherwise use an existing Hermes
  secret or `op://` reference. Requests with `storage: env` are refused before any I/O.
- Document how to install and sign in the 1Password CLI, and what `op` is used for.
- Record manual end-to-end verification (Desktop page, connector download, live 1Password item
  creation, updates with health check, publish with approval) and the paths not yet tried live.
- Rename the Desktop sidebar item, page title, plugin name and ⌘K commands from "WordPress" to
  "WP Manager", so the WordPress trademark is only used descriptively.

## 0.1.2

- Add explicit Desktop connection-string storage choice: create a new 1Password item in a
  selected vault (recommended when available), use an existing secret/reference, or opt into .env.
- Enumerate vault metadata only through configured `op_binary`; send item JSON on stdin, never
  secret argv. Require ID-based read-back equality and WordPress authentication before registry
  commit. Fail closed without .env fallback; preserve existing sites and vault items.
- Report actual storage after connection. CLI/auth unavailable states offer Retry and explicit
  alternatives. Failed creation/verification may leave a new vault item for manual removal.
- Add swapped-subprocess privacy, ordering, failure and Desktop storage regression coverage.

## 0.1.1

- Opening Desktop site details automatically loads previously missing pending-update records
  through the existing scan endpoint with `refresh: false`. Connection-summary counts stay
  visible while loading; list and detail then read the same cached scan.
- Missing, loading, failed and successfully empty detail lists are separate states. Failed
  queries stop interval retries and offer Retry. Rescan still requests `refresh: true` and now
  surfaces per-site `ok: false` failures.
- Disable update controls without a usable list; reject empty or stale confirmation batches
  instead of reporting a no-op success. Health checking remains separate.
- Add deterministic deferred-query/render regressions and a read-only backend contract test.
