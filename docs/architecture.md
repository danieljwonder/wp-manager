# Architecture

wp-manager provides WordPress maintenance and content tools in Hermes chat and a
fleet view in Hermes Desktop. It has no backup, rollback, staging, client-reporting
or malware-scanning subsystem.

## Components

| Path | Responsibility |
|---|---|
| `plugin.yaml`, `__init__.py` | Hermes manifest and registration: nine tools, a `pre_tool_call` hook and the bundled usage skill |
| `schemas.py`, `tools.py` | Tool input schemas and JSON-result handlers |
| `approval.py` | Interactive approval decisions and unattended-session guards |
| `core/` | Shared registry, credentials, HTTP transport, normalization, operations, health and state |
| `dashboard/plugin_api.py` | FastAPI router mounted by Hermes at `/api/plugins/wp-manager/` |
| `desktop/plugin.js` | Desktop SDK page, site dialogs, scans, confirmations and connector downloads; no build step |
| `connector/website-manager-hermes-connector/` | Standard WordPress plugin with REST routes, optional Abilities API integration and admin connection UI |
| `skills/wp-manager/SKILL.md` | Agent guidance for updates, credentials and content editing |
| `tests/`, `e2e/`, `scripts/record_fixtures.py` | Unit/contract tests, disposable Playground harness and fixture recorder |

The core HTTP client uses Python's standard library. The dashboard router uses
FastAPI supplied by Hermes; backend/API tests also need its test dependencies.
The web dashboard manifest hides its tab: there is no separate web UI bundle.
For remote Hermes connections, the backend and Desktop page are installed on their
respective machines; see the [installation guide](../README.md#install).

## Data and credentials

Paths are resolved through Hermes's active home, not a fixed installation path:

- `$HERMES_HOME/wp-manager/sites.json`: addresses, usernames, tags and credential
  references, never Application Password values.
- `$HERMES_HOME/wp-manager/state.json`: cached status, update offers and health.
- `$HERMES_HOME/wp-manager/actions.jsonl`: site add/remove, update and content-save
  audit entries, with secret redaction.

Credentials resolve through Hermes secrets using `WPM_<SITE_ID>_APP_PASSWORD`, or
through a direct `op://Vault/Item/field` reference using the configured trusted
`op_binary`. wp-manager never reads or writes the Hermes `.env` file (catalog
rule 11); a user who wants `.env` storage adds the `WPM_*_APP_PASSWORD` line
themselves and Hermes resolves it as a normal secret.

Explicit 1Password creation enumerates vault metadata, creates a new Login item
with password JSON on stdin, compares an ID-based read-back exactly, then
authenticates against the original validated WordPress URL before registry save.
It does not overwrite existing items. Verification failure
can leave a new vault item requiring manual removal. Removing a site does not
revoke its Application Password or delete its secret.

Connection strings contain a password, not encryption: `wpmh1:` prefixes base64
JSON with `v`, `url`, `user`, and `app_password`. Use the Desktop dialog rather than
chat history. See [credential setup](../README.md#where-the-password-is-kept).

## Operations and trust boundaries

Tools and the Desktop backend call the same core services. Scans read connector
status and offers; `refresh: true` requests fresh WordPress update checks. Merely
opening detail reads cached state; Desktop hydrates a missing item list with one
`refresh: false` scan. Health is checked separately, not inferred from an empty
update list.

The tool approval hook gates updates, publishing, edits to published/scheduled
content and credential binding. Status lookup failures fail closed. With
`require_approval: true` (default), gated updates/content changes are blocked in
cron and webhook/API-server sessions. Turning it off removes those update/content
guards, including unattended blocking; site-add credential gates remain enabled.
Draft creation is not approval-gated. Desktop confirmation is the approval for
backend writes; these API routes do not invoke the tool hook. Protect access to
the Hermes backend accordingly.

The client refuses non-local plain HTTP unless explicitly opted in, rejects URL
userinfo and confines authenticated redirects to the same origin without HTTPS
downgrade. Redaction covers known credentials, authorization headers and
connection strings; tests are not a guarantee against every possible disclosure.
Treat site responses and content as untrusted input.

The connector uses WordPress Application Password authentication, per-operation
capabilities, admin nonces and an atomic option-backed upgrader lock. REST and
Abilities call the same service. Major core updates are disabled by default.
See the [connector API](connector-api.md) for request identifiers, response shapes,
capabilities and partial-failure behavior.

After update attempts the core runs homepage, REST and connector health probes.
Degraded/down health prevents a success result and adds an alert. These probes
are limited diagnostics, not proof that all site functionality works. Content
uses core WordPress post/page endpoints; new content defaults to drafts and
page-builder body edits require an explicit override after a heuristic warning.

## Limitations and verification

There are no backups or automatic rollback. Restore planning belongs to the site
operator. Host-isolated backend calls may time out while a longer update keeps
running: inspect scan/health before retrying a write. Real Desktop rendering,
live-vault integration and production behavior are not established by unit tests.
See [testing and recorded evidence](testing.md) for reproducible checks and
remaining coverage gaps.

The Hermes plugin is MIT licensed; the bundled WordPress connector is
GPLv2-or-later. Author attribution and project URLs are retained in the manifests
and [license](../LICENSE).
