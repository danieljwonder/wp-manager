# Testing and verification

## Local checks

Use a Python environment with Hermes and its dashboard test dependencies available
(FastAPI, Starlette and HTTPX). Core transport and unit-test discovery use the
standard library; API and SDK-surface checks use Hermes dependencies. Node is
required for the Desktop renderer regressions; inspect any skipped tests.

From the repository root:

```bash
(cd tests && python -m unittest)
node --check desktop/plugin.js
hermes plugins validate .
hermes plugins doctor . --ci
git diff --check
```

Run tests from `tests/` so the root `tools.py` does not shadow Hermes's own `tools`
package. Validate a clean checkout or tracked-file export: ignored research files,
harness credentials and generated outputs are not part of the plugin package and
can produce unrelated scanner findings. Validation imports/registers the plugin;
it is not an instruction to install or enable it in a running Hermes instance.

Tests cover tool inputs/results, approval scope and unattended guards, registry
permissions, secret redaction, same-origin redirects, refusal of `.env` storage,
1Password subprocess ordering/failures, update-result normalization, API contracts
and Desktop loading/storage states. Node rendering uses strict SDK stubs, **not**
a running Hermes Desktop application. Security-scan success is not a security audit.

## Disposable WordPress checks

The [Playground harness](../e2e/README.md) starts a throwaway site, installs old
extension versions and performs real update writes. Never point it or the recorder
at a production site. It needs npm/WordPress.org access; versions and update offers
can change over time.

After starting a fresh Playground, the joined Python/connector recorder can check
plugin/theme updates and draft creation/edit/read-back:

```bash
python scripts/record_fixtures.py --creds-file .e2e-run/creds.json \
  --apply plugin:classic-editor/classic-editor.php \
  --apply theme:twentytwentyfour --content --out .e2e-run/recorded.json
```

The recorder rejects unsuccessful operation/health results, verifies real
plugin/theme version changes and retained activity through independent core REST
reads, and checks draft title/body/status after editing. Its independent version
verification supports plugin/theme updates, not every update type. Review redaction
before promoting a recording into tracked fixtures; never commit credentials.

## Fixture provenance and historical results

- `tests/fixtures/site.json` is hand-written and marked `source: synthetic`.
- `tests/fixtures/recorded.json` is a real joined Python-core/connector Playground
  recording: 13 endpoints / 28 responses, including plugin/theme writes and draft
  creation/edit/read-back. Request credentials are not recorded.
- `tests/fixtures/connector-samples/` and `e2e/samples/` preserve redacted connector
  responses, including partial failures, authentication errors and update locks.
  The `http_status`/`request`/`body` wrapper is recording metadata, not wire format.
- Connector runs recorded on **2026-11-05 UTC**, version **0.1.0**, reported
  48/48 checks on WordPress 7.1.4 / PHP 8.3.36, 44/44 on WordPress 6.5.14 /
  PHP 7.4.34-dev, and 10/10 for a real 6.5.14 → 7.1.4 core upgrade.
  [Connector results](../e2e/CONNECTOR_REPORT.md) retain the detailed evidence,
  errors and limitations. These version numbers are historical, not current-release claims.
- The original joined run reported 13/13 recorder steps and 119 unit tests passing;
  the 0.1.1 detail-loading regression run reported 121 passing. Earlier implementation
  checks reported 117 passing before joined write coverage was added. Those are
  historical counts, not a statement of the current suite size.
- Historical clean-package Hermes validation and doctor passed with nine tools and
  one hook. PHP-wasm syntax/compile and actual `php -l` checks passed for all ten
  connector PHP files on both recorded runtimes; no PHPCS/WPCS certification is claimed.

The `.env` writer was removed in 0.1.3; a regression test asserts the module has
no writer and that `.env` storage requests are refused with no I/O. Initial detail-loading tests likewise reproduced
an incorrect all-clear message before the 0.1.1 fix. These regressions remain in
the suite; successful results do not remove the underlying risk of future regressions.

## Known gaps

- Manually verified once, end to end, on Hermes 0.22.5 (Umbrel backend, Hermes
  Desktop on macOS as a remote client) against one live production site (the
  recorded demo): Desktop page rendering, **Get connector** download and install,
  connection-string add with a new 1Password item created through a
  service-account `op` wrapper, scan, Update all with the post-update health
  check, and publishing a post from chat through the approval prompt.
- Not yet verified live: the 1Password desktop-app (biometric) sign-in path,
  Desktop running against a local Hermes, Windows/Linux Desktop, more than one
  site at a time, and the Hermes-secret and direct `op://` reference add paths
  (covered by unit tests only).
- Host isolation was documented to buffer backend calls and return HTTP 504 after
  60 seconds while a longer update/scan continues. No asynchronous job/poll API is
  implemented. After a timeout, scan and check health before retrying update POSTs.
- No production validation, backup/restore, rollback, staging or malware scanning.
  Page-builder detection is heuristic; REST can omit externally stored builder data.
- Connector coverage does not establish multisite/uninstall behavior, actual
  minor-core package upgrades, failed-download rollback/reactivation fallback,
  paused-extension/recovery-mode branches, the graphical copy button or external
  WordPress MCP Adapter interoperability. Core Abilities discovery/execution was
  tested separately from that adapter.
- Playground's stale `.maintenance` worker state and raw PHP-wasm certificate-probe
  failures are recorded in the connector results, not suppressed as successful
  production responses. Translation offers are not deterministic on a fresh site.
- Catalog admission for direct `op` CLI use (reads, vault metadata, item creation)
  still needs review; passing local validation is not catalog approval.
