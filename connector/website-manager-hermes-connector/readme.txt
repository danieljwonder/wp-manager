=== Website Manager: WP Connector for Hermes ===
Contributors: danieljwonder
Tags: updates, maintenance, rest api, abilities, ai agent
Requires at least: 6.5
Tested up to: 7.1
Requires PHP: 7.4
Stable tag: 0.1.0
License: GPLv2 or later
License URI: https://www.gnu.org/licenses/gpl-2.0.html

Lets the Hermes agent check for and run plugin, theme, core and translation updates on your site, using WordPress Application Passwords.

== Description ==

Website Manager: WP Connector for Hermes connects your site to [Hermes](https://hermes-agent.nousresearch.com/), an AI agent you run yourself, so it can keep the site updated alongside your other sites.

What it adds:

* A small authenticated REST API (`/wp-json/website-manager-hermes/v1/`) to read site status, list available updates, run updates, read a health self-report and read a log of recent actions.
* On WordPress 6.9 and later, the same operations as Abilities (`website-manager-hermes/get-status`, `website-manager-hermes/list-updates`, `website-manager-hermes/run-updates`), so the WordPress MCP Adapter and other AI clients can use them.
* A page under **Tools → Hermes Connector** that checks requirements, creates a dedicated Application Password and shows a one-time connection string to paste into Hermes, lets you allow or block core updates, and lists recent actions.

How it works:

* Hermes signs in with a WordPress **Application Password**, never your login password. You can revoke it any time under Users → Profile → Application Passwords.
* Updates run through WordPress's own upgraders (the same code as Dashboard → Updates). Active plugins stay active and the active theme stays active.
* Every request checks the matching capability (`update_plugins`, `update_themes`, `update_core`, `update_languages`). Only one update run can happen at a time.
* Major WordPress core updates are blocked by default; enable them on the Tools page if you want Hermes to run them.

What it does not do: backups, staging, malware scanning or reports. **Take or confirm a backup with your host before running bulk updates.**

= Privacy and external services =

This plugin does not track you, does not phone home and sends no data anywhere. The only outgoing requests it causes are WordPress's own update checks and package downloads to WordPress.org (api.wordpress.org and downloads.wordpress.org), made by WordPress core functions when Hermes asks for a fresh update check or runs an update. These are the same requests WordPress makes on its own when it checks for updates. See the [WordPress.org privacy policy](https://wordpress.org/about/privacy/).

The action log (last 50 actions: time, user, action, items, result) is stored in your database and deleted when you uninstall the plugin. It never contains passwords.

== Installation ==

1. Upload the plugin through Plugins → Add New → Upload Plugin, or copy the `website-manager-hermes-connector` folder to `wp-content/plugins/`.
2. Activate it.
3. Go to Tools → Hermes Connector and click **Create connection for Hermes**.
4. Copy the connection string (shown once) and paste it into the Hermes Desktop "Add site" dialog.

Requirements: HTTPS (or a local development environment) so Application Passwords are available, and a server where WordPress can update itself without asking for FTP credentials.

== Frequently Asked Questions ==

= Where is the connection string stored? =

Nowhere. The plugin creates a normal Application Password and shows the connection string once. WordPress stores only a hash of the password.

= How do I disconnect Hermes? =

Revoke the "Hermes (website-manager)" Application Password under Users → Profile → Application Passwords. Deactivating the plugin also removes the connector's routes.

= Can Hermes update WordPress core? =

Minor core updates are allowed by default and major ones are blocked. Change this on Tools → Hermes Connector.

= Does it work on multisite? =

The routes are available on multisite, where update capabilities belong to super admins, as in core.

== Changelog ==

= 0.1.0 =
* First release: status, updates, health and log REST routes; Abilities API registration; Tools → Hermes Connector page.

== Upgrade Notice ==

= 0.1.0 =
First release.
