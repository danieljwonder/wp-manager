# WordPress Playground end-to-end harness

No Docker or native PHP required. Requires Node/npm, curl, Bash, Python 3 (only for ad-hoc credential inspection), network access to npm and WordPress.org. Run from the repository root:

```bash
e2e/start-playground.sh
node e2e/verify.mjs
e2e/stop-playground.sh
e2e/lint.sh
```

The start script uses `npx -y @wp-playground/cli@latest server`, mounts the connector, installs Classic Editor **1.6.3** (active), Hello Dolly **1.6** (inactive), and Twenty Twenty-Four **1.0** (active), sets `WP_ENVIRONMENT_TYPE=local`, and creates an admin Application Password via blueprint `runPHP`. Credentials are in **`.e2e-run/creds.json`**, inside a private, gitignored directory. Never commit them. Admin UI login for this throwaway site is `admin` / `password`. Do not expose this test server publicly.

`verify.mjs` checks actual REST responses, pending versions from WordPress.org, real package downloads/installations, independent version/activity confirmation through core REST, the update lock, health/log, error paths, Abilities API list + execution, and the nonce-protected **Tools → Hermes Connector → Create connection for Hermes** flow. It decodes the `wpmh1:` string and uses that new credential to authenticate. Samples are written to `.e2e-run/samples/` with passwords redacted; summary to `.e2e-run/results.json`.

Minimum versions and a real core update:

```bash
e2e/stop-playground.sh
WMHC_E2E_WP=6.5 WMHC_E2E_PHP=7.4 e2e/start-playground.sh
WMHC_E2E_LABEL=wp65-php74 node e2e/verify.mjs
node e2e/verify-core.mjs
e2e/stop-playground.sh
```

`verify-core.mjs` verifies that a major core target is blocked by default, enables it through the actual nonce-protected admin settings form, then installs the offered WordPress release and checks `/status`, `/health`, and the homepage. Start with an old WP version, not `latest`.

Options: `WMHC_E2E_PORT` (9400), `WMHC_E2E_PHP` (8.3), `WMHC_E2E_WP` (latest), `WMHC_E2E_TIMEOUT` (420 seconds), `WMHC_E2E_LOCALE` (optional, e.g. `de_DE`), `WMHC_E2E_LABEL` (suffix on sample/result directories), `WMHC_LINT_PHP` (`"8.3 7.4"`). WordPress `6.5` resolves to the latest patch on that branch, currently 6.5.14. The script injects `preferredVersions` into a generated blueprint because CLI flags alone were ignored when a blueprint was supplied.

`lint.sh` uses the CLI's PHP-wasm runtime to parse/compile all 10 plugin PHP files and run actual `php -l` on each, under both PHP 8.3 and 7.4. It does not claim to be a PHPCS/WPCS run.

## Proven

* WP **7.1.4 / PHP 8.3.36**: **48/48** checks passed.
* WP **6.5.14 / PHP 7.4.34-dev**: **44/44** checks passed (Abilities API correctly absent).
* Real core **6.5.14 → 7.1.4** update: **10/10** checks passed.
* Real plugin **1.6.3 → 1.7.0**, Hello Dolly **1.6 → 1.7.2**, theme **1.0 → 1.6**. Active/inactive state preserved.
* A real German Hello Dolly language-pack update also downloaded and installed successfully (manual curl run, recorded sample).
* Playground networking supports core's WordPress.org update checks **and** real update package downloads. Nothing mocked.

See [`CONNECTOR_REPORT.md`](CONNECTOR_REPORT.md) for exact results/limitations and [`../docs/connector-api.md`](../docs/connector-api.md) for the precise JSON contract. Checked-in [`samples/`](samples/) are redacted recordings; refresh them only from a real run.

## Playground quirk

With the CLI's default six workers, a worker can briefly retain stale `.maintenance` state after an update: a request returns HTTP 200 with HTML containing `require(/wordpress/.maintenance): Failed to open stream` / a fatal error, even though the upgrader removed the file. The verifier reports that response, waits for 12 clean health probes, then performs the real assertions. It does **not** fake a healthy response. On a real host, treat non-JSON/unhealthy responses seriously; this harness accommodation is not client behavior.

The server is ephemeral. Start afresh before each full verification (otherwise the old plugin/theme versions are already updated). Stop with the provided script, which terminates the npx process group, not just its launcher. The completed work leaves no Playground server running.
