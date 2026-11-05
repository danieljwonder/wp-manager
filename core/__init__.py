"""wp-manager core: pure logic and HTTP transport shared by the agent tools (tools.py) and the
dashboard backend (dashboard/plugin_api.py).

Every module in this package uses package-relative imports only, so the same files load both as
``<hermes plugin package>.core`` (agent side) and as a standalone package that plugin_api.py loads
by path (dashboard side).
"""

PLUGIN_ID = "wp-manager"
VERSION = "0.1.0"
CONNECTOR_NAMESPACE = "website-manager-hermes/v1"
CONNECTOR_SLUG = "website-manager-hermes-connector"
