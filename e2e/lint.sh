#!/usr/bin/env bash
# PHP-lints the connector under PHP-wasm (Playground CLI `php` command).
# Runs `php -l` on every file plus a full-parse/compile pass (lint.php),
# under PHP 8.3 and the minimum supported PHP 7.4.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(dirname "$HERE")"
CONNECTOR="$ROOT/connector/website-manager-hermes-connector"
VERSIONS="${WMHC_LINT_PHP:-8.3 7.4}"
status=0

for v in $VERSIONS; do
	echo "== PHP $v =="
	# Parse + compile every file in one PHP process.
	if ! npx -y @wp-playground/cli@latest php --php "$v" \
		--wordpress-install-mode do-not-attempt-installing \
		--mount "$CONNECTOR:/connector" --mount "$HERE:/e2e" \
		-- /e2e/lint.php /connector 2>&1 | grep -v '^npm notice'; then
		status=1
	fi
	# And the real `php -l` per file.
	while IFS= read -r -d '' f; do
		rel="${f#"$CONNECTOR"/}"
		out="$(npx -y @wp-playground/cli@latest php --php "$v" \
			--wordpress-install-mode do-not-attempt-installing \
			--mount "$CONNECTOR:/connector" -- -l "/connector/$rel" 2>&1 | grep -v '^npm notice' || true)"
		if grep -q "No syntax errors detected" <<<"$out"; then
			echo "php -l OK   $rel"
		else
			echo "php -l FAIL $rel"; echo "$out"; status=1
		fi
	done < <(find "$CONNECTOR" -name '*.php' -print0 | sort -z)
done
exit $status
