---
name: wp-manager
description: Use when managing WordPress sites (updates, health, posts, pages) via wp_* tools.
version: 0.1.4
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
   at a time and stop at the first unhealthy result.
4. Read the result. If it has an `alert` (health degraded or down) or `warning` (some items
   failed), say so first, plainly, with the failing signals. Never call that run a success. Tell
   the user to check the site and restore from backup if it is broken.
5. `wp_health_check` checks a site on demand: home page, REST API, connector self-report.

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
