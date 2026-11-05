#!/usr/bin/env bash
# Boots a throwaway WordPress in WordPress Playground (PHP-wasm, no Docker,
# no native PHP) with the connector mounted, old plugin/theme versions
# installed, and an admin Application Password written to .e2e-run/creds.json.
#
# Env:
#   WMHC_E2E_PORT  (default 9400)
#   WMHC_E2E_PHP   (default 8.3; e.g. 7.4 to test the minimum PHP)
#   WMHC_E2E_WP    (default latest; e.g. 6.5 to test the minimum WordPress)
#   WMHC_E2E_LOCALE  (optional, e.g. de_DE: switch the site language after the
#                    old plugins/themes are installed, to exercise translation updates)
#   WMHC_E2E_TIMEOUT seconds to wait for boot (default 420)
set -euo pipefail
umask 077 # Credentials and logs must be private to the harness user.

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
RUN="$ROOT/.e2e-run"
PORT="${WMHC_E2E_PORT:-9400}"
PHP_VERSION="${WMHC_E2E_PHP:-8.3}"
WP_VERSION="${WMHC_E2E_WP:-latest}"
TIMEOUT="${WMHC_E2E_TIMEOUT:-420}"
CONNECTOR="$ROOT/connector/website-manager-hermes-connector"

mkdir -p "$RUN"
chmod 700 "$RUN"

if [[ -f "$RUN/playground.pid" ]] && kill -0 "$(cat "$RUN/playground.pid")" 2>/dev/null; then
	echo "Playground already running (pid $(cat "$RUN/playground.pid")). Run e2e/stop-playground.sh first." >&2
	exit 1
fi

rm -f "$RUN/creds.json" "$RUN/creds.error"

# The CLI ignores --php/--wp when the blueprint has no preferredVersions
# (observed: it then serves PHP 8.5 + latest WP), so inject them.
node -e '
const fs = require("fs");
const [src, dst, php, wp, locale] = process.argv.slice(1);
const bp = JSON.parse(fs.readFileSync(src, "utf8"));
bp.preferredVersions = { php, wp };
if (locale) {
  const i = bp.steps.findIndex((s) => s.step === "runPHP");
  bp.steps.splice(i, 0, { step: "setSiteLanguage", language: locale });
}
fs.writeFileSync(dst, JSON.stringify(bp, null, 2));
' "$HERE/blueprint.json" "$RUN/blueprint.generated.json" "$PHP_VERSION" "$WP_VERSION" "${WMHC_E2E_LOCALE:-}"

echo "Starting Playground: WP=$WP_VERSION PHP=$PHP_VERSION port=$PORT"
# setsid: own process group so stop-playground.sh can kill npx and its children.
setsid nohup npx -y @wp-playground/cli@latest server \
	--port "$PORT" \
	--php "$PHP_VERSION" \
	--wp "$WP_VERSION" \
	--blueprint "$RUN/blueprint.generated.json" \
	--mount-before-install "$CONNECTOR:/wordpress/wp-content/plugins/website-manager-hermes-connector" \
	--mount-before-install "$RUN:/e2e-run" \
	>"$RUN/playground.log" 2>&1 </dev/null &
echo $! >"$RUN/playground.pid"
echo "$PORT" >"$RUN/playground.port"

deadline=$(( $(date +%s) + TIMEOUT ))
while (( $(date +%s) < deadline )); do
	if ! kill -0 "$(cat "$RUN/playground.pid")" 2>/dev/null; then
		echo "Playground exited during boot. Log:" >&2
		tail -40 "$RUN/playground.log" >&2
		exit 1
	fi
	if [[ -f "$RUN/creds.error" ]]; then
		echo "Application password creation failed: $(cat "$RUN/creds.error")" >&2
		exit 1
	fi
	if [[ -f "$RUN/creds.json" ]]; then
		code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$PORT/wp-json/" || true)"
		if [[ "$code" == "200" ]]; then
			echo "Ready: http://127.0.0.1:$PORT (credentials in $RUN/creds.json, admin/password for wp-admin)"
			exit 0
		fi
	fi
	sleep 3
done

echo "Timed out after ${TIMEOUT}s waiting for Playground. Log tail:" >&2
tail -40 "$RUN/playground.log" >&2
exit 1
