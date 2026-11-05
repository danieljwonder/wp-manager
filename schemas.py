"""Tool schemas (OpenAI function format) for the wp_manager toolset."""

SITE = {"type": "string", "description": "Site id from wp_sites (a URL or exact label also works)."}
CONTENT_TYPE = {"type": "string", "enum": ["post", "page"]}

SITES = {
    "name": "wp_sites",
    "description": (
        "List the WordPress sites registered in wp-manager with their cached status: WordPress/PHP/connector "
        "versions, pending plugin/theme/core/translation update counts, last health verdict and last check time. "
        "Cached data; run wp_updates_scan to refresh."
    ),
    "parameters": {"type": "object", "properties": {
        "tag": {"type": "string", "description": "Only sites with this tag."},
    }},
}

SITE_ADD = {
    "name": "wp_site_add",
    "description": (
        "Register a WordPress site. The site needs the 'Website Manager: WP Connector for Hermes' plugin and a "
        "WordPress Application Password. Verifies the connection before saving. Prefer asking the user to paste "
        "the connection string into the Hermes Desktop 'Add site' dialog (WordPress page) rather than into chat. "
        "Without a connection string, the password must already be stored as a Hermes secret, by default "
        "WPM_<SITE_ID>_APP_PASSWORD (e.g. `hermes secrets onepassword set WPM_MYSITE_APP_PASSWORD op://...`)."
    ),
    "parameters": {"type": "object", "properties": {
        "url": {"type": "string", "description": "Site address, e.g. https://example.com"},
        "username": {"type": "string", "description": "WordPress username that owns the Application Password."},
        "label": {"type": "string"},
        "id": {"type": "string", "description": "Optional short id (slug); derived from the URL by default."},
        "tags": {"type": "array", "items": {"type": "string"}},
        "credential": {
            "type": "object",
            "description": 'Where the Application Password lives: {"env": "WPM_<ID>_APP_PASSWORD"} (a Hermes '
                           'secret: .env, 1Password, Bitwarden...) or {"op": "op://Vault/Item/field"}.',
            "properties": {"env": {"type": "string"}, "op": {"type": "string"}},
        },
        "connection_string": {"type": "string",
                              "description": "wpmh1:... string from Tools → Hermes Connector. Never repeat it back."},
        "allow_insecure": {"type": "boolean",
                           "description": "Allow plain http:// (password sent unencrypted). Default false."},
    }},
}

SITE_REMOVE = {
    "name": "wp_site_remove",
    "description": "Remove a site from wp-manager (does not touch the site or revoke its Application Password).",
    "parameters": {"type": "object", "properties": {"site": SITE}, "required": ["site"]},
}

UPDATES_SCAN = {
    "name": "wp_updates_scan",
    "description": (
        "Fetch status and available updates (plugins, themes, core, translations) from sites, in parallel. "
        "Read-only. refresh=true makes WordPress re-check wordpress.org first (slower)."
    ),
    "parameters": {"type": "object", "properties": {
        "sites": {"type": "array", "items": {"type": "string"}, "description": "Site ids; default all."},
        "tag": {"type": "string", "description": "Only sites with this tag."},
        "refresh": {"type": "boolean"},
    }},
}

UPDATE_APPLY = {
    "name": "wp_update_apply",
    "description": (
        "Install updates on one site, then run a health check and rescan. Requires the user's approval. "
        "wp-manager takes NO backups: confirm the user has a recent host backup before bulk updates. "
        "If the result has an 'alert' (health degraded/down) tell the user loudly; never call it a success."
    ),
    "parameters": {"type": "object", "properties": {
        "site": SITE,
        "type": {"type": "string", "enum": ["plugin", "theme", "core", "translations"]},
        "items": {
            "description": 'Plugin files (akismet/akismet.php) or slugs, theme stylesheets, a core version, or "all".',
            "anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "string", "enum": ["all"]}],
        },
        "exclude": {"type": "array", "items": {"type": "string"}, "description": "Items to skip when items is all."},
    }, "required": ["site", "type"]},
}

HEALTH_CHECK = {
    "name": "wp_health_check",
    "description": (
        "Check a site's health now: home page (status, response time, 'critical error' text), REST API, and the "
        "connector's self-report (fatal errors, paused plugins, recovery mode, database). Verdict ok|degraded|down."
    ),
    "parameters": {"type": "object", "properties": {"site": SITE}, "required": ["site"]},
}

CONTENT_LIST = {
    "name": "wp_content_list",
    "description": "List posts or pages on a site (id, title, status, slug, link, modified).",
    "parameters": {"type": "object", "properties": {
        "site": SITE,
        "type": CONTENT_TYPE,
        "search": {"type": "string"},
        "status": {"type": "string", "description": "publish, draft, pending, private, future, trash or any."},
        "per_page": {"type": "integer", "minimum": 1, "maximum": 100},
    }, "required": ["site", "type"]},
}

CONTENT_GET = {
    "name": "wp_content_get",
    "description": (
        "Get one post or page for editing: title, status, slug, raw content (block markup for block sites), "
        "excerpt, link, modified, and the editor kind (blocks/classic/page_builder). Always read before editing."
    ),
    "parameters": {"type": "object", "properties": {
        "site": SITE, "type": CONTENT_TYPE, "id": {"type": "integer"},
    }, "required": ["site", "type", "id"]},
}

CONTENT_SAVE = {
    "name": "wp_content_save",
    "description": (
        "Create (no id) or update (id) a post or page. New items default to status draft. Publishing, or editing "
        "an already-published item, requires the user's approval. Send Gutenberg block markup for block-editor "
        "sites; when editing, start from wp_content_get's content and keep its structure. Page-builder pages "
        "(Elementor etc.) are refused unless the user insists (confirm_page_builder)."
    ),
    "parameters": {"type": "object", "properties": {
        "site": SITE,
        "type": CONTENT_TYPE,
        "id": {"type": "integer", "description": "Existing item id; omit to create."},
        "title": {"type": "string"},
        "content": {"type": "string", "description": "HTML or block markup."},
        "excerpt": {"type": "string"},
        "status": {"type": "string", "enum": ["draft", "pending", "publish", "private"]},
        "slug": {"type": "string"},
        "confirm_page_builder": {"type": "boolean",
                                 "description": "Only when the user explicitly insists on editing a page-builder body."},
    }, "required": ["site", "type"]},
}

ALL = (SITES, SITE_ADD, SITE_REMOVE, UPDATES_SCAN, UPDATE_APPLY, HEALTH_CHECK, CONTENT_LIST, CONTENT_GET, CONTENT_SAVE)
