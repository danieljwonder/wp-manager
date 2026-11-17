---
name: wp-manager
description: Use when managing WordPress sites (updates, health, posts, pages) via wp_* tools.
version: 0.2.1
metadata:
  hermes:
    tags: [wordpress, updates, content, websites]
---

# WordPress sites with wp-manager

The `wp_*` tools manage WordPress sites that run the **Website Manager: WP Connector for Hermes**
plugin. They cover plugin/theme/core/translation updates (with a health check after every run) and
creating or editing posts and pages. wp-manager takes **no backups**.

## Connecting a site

1. On the WordPress site: install and activate *Website Manager: WP Connector for Hermes*, open
   **Tools → Hermes Connector**, click **Create connection for Hermes** and copy the `wpmh1:…` string.
2. Best: the user pastes that string into **Hermes Desktop → WP Manager → Add site**. It contains a
   password, so steer users away from pasting it into chat. If they already did, call `wp_site_add`
   only after confirming explicit storage: `storage: onepassword` with the user's selected
   `vault_id`, or `storage: existing` with `credential`. There is no .env option: wp-manager never
   writes the .env file. Never repeat the string or any part of it back. Prefer Desktop for vault
   selection. New 1Password items need the op CLI (README: Set up the 1Password CLI), require
   approval, and are ID-reference/read-back/WordPress-auth verified before registry save. A failed attempt may leave a new item for manual
   removal. Existing sites are not migrated. Report the returned `credential_storage` accurately.
3. Users with a password manager can store the Application Password as a Hermes secret first
   (`hermes secrets onepassword set WPM_<SITE_ID>_APP_PASSWORD op://Vault/Item/password`) and call
   `wp_site_add` with `url` and `username` only. Every `wp_site_add` asks the user to approve the
   URL and the secret it will receive. Ids that would share another site's secret name (`my-blog`
   vs `my--blog`) are refused: pick a clearly different id.
4. `http://` sites are refused unless the user explicitly accepts `allow_insecure: true` (its own
   approval prompt). Localhost is fine.

## Updates

1. `wp_updates_scan` (add `refresh: true` to make WordPress re-check wordpress.org). Summarise what
   is pending per site. Point out major core versions, incompatible items and premium plugins
   without a package.
2. **Before any update, ask whether the user has a recent backup from their host.** wp-manager
   takes none.
3. `wp_update_apply` with one `type` per call (`plugin`, `theme`, `core`, `translations`). It is
   approval-gated: Hermes asks the user. Prefer explicit `items`. On several sites, go one site
   at a time. A failed update on one site must not block already-authorised updates on independent
   sites. A single item failure, such as an expired premium licence or missing package, must not
   block the other authorised items on the same site when the site itself remains healthy. Report
   failed items plainly and continue the rest. If the site's health degrades or it goes down, stop
   further updates on that site, report the failing signals, and continue on other sites. Do not
   automatically retry a failed item or expand the authorised scope. Stop the whole batch only if
   the failure reveals a shared risk affecting the remaining sites or the user asks you to.
4. Read the result. If it has an `alert` (health degraded or down) or `warning` (some items
   failed), say so first, plainly, with the failing signals. Never call that run a success. Tell
   the user to check the site and restore from backup if it is broken.
5. `wp_health_check` checks a site on demand: home page, REST API, connector self-report.

## Update alerts

When the user wants to hear about new updates ("tell me about WordPress updates every morning"):

1. Call `cronjob` with `action: list` first. If a job named "WP Manager update alerts" exists,
   update it instead of creating another.
2. `wp_updates_watch` with `action: setup` (optional `sites` or `tag`). It installs the check script
   and returns the exact `cronjob` arguments.
3. Ask how often and where to send alerts if they did not say, then call `cronjob` with those
   arguments, their schedule and `deliver` target. Keep `no_agent: true`: the script's output is
   the message and no model runs. Never add a prompt or skills to that job.
4. Tell them: the first alert lists what is already pending, then only new updates; nothing new
   sends nothing; alerts never install anything; pause, change or remove it like any scheduled job.

`wp_updates_watch` with `action: status` shows the watched sites, last check and failing sites.
An alert is never approval to update: when the user replies to one, follow the Updates steps above.

## Content

- Always `wp_content_get` before editing, and edit the returned `content`. Keep its structure
  intact.
- `editor: "blocks"`: write Gutenberg block markup (`<!-- wp:paragraph --><p>…</p><!-- /wp:paragraph -->`,
  `wp:heading`, `wp:list`, `wp:image`, …). Keep existing blocks and their attributes. Do not
  flatten them to plain HTML.
- `editor: "classic"`: HTML is fine.
- `editor: "page_builder"` (Elementor, Divi, WPBakery, Beaver Builder, …): the raw content is not
  what renders. Tell the user to edit it in wp-admin. Only change title/excerpt/slug here. Set
  `confirm_page_builder: true` for a body edit only if they explicitly insist after the warning.
  Advanced Custom Fields and similar meta-driven pages behave the same way.
- New posts and pages are saved as **drafts** by default. Publishing, or editing anything already
  published, needs the user's approval through Hermes. Do not set `status: publish` unless the
  user asked for it. Share the draft link or suggest a preview in wp-admin.

## Safety notes

- Never ask the user to type their WordPress admin password. wp-manager only uses Application
  Passwords, which can be revoked under Users → Profile → Application Passwords.
- With `require_approval: true` (default), unattended runs (cron) cannot install updates or
  publish. Disabling that setting removes those guards; credential-binding and new-item storage
  gates remain enabled. Draft creation is not gated. Scans and health checks are fine in cron.
- Every write action is logged to `$HERMES_HOME/wp-manager/actions.jsonl`.
