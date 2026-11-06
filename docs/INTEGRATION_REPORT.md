# Integration verification

## 0.1.1 initial detail-loading fix

- Reproduced the exact fresh-site mismatch in the strict Node renderer: nonzero connection
  summary counts, `last_checked: null`, fabricated empty item arrays, and the incorrect
  “Everything is up to date” message. The regression failed before the fix.
- Fixed in Desktop only, using existing backend routes. An initial read-only scan with
  `refresh: false` hydrates missing item records; successful detail and summary reads share
  the cached scan. Ordinary detail polling does not repeat WordPress scans. Query errors
  stop interval retries and expose Retry. Rescan remains `refresh: true`.
- Covered deferred loading, summary counts, populated/empty success, no-count-but-unscanned
  state, `ok:false`, malformed results, stale data on query error, cached scan errors, no-op
  and stale confirmations. Added API contract coverage proving only two WordPress GETs
  (status and cached offers), matching counts/items and untouched health.
- `cd tests && /opt/hermes/.venv/bin/python -m unittest`: **121 passed**.
- `node --check desktop/plugin.js` and `git diff --check`: passed.
- Clean-copy Hermes validation (`--install-deps`) and doctor (`--ci`): passed; security
  safe, SDK surface clean, **9 tools / 1 hook**. Excluded `.git`, `.research`, `.e2e-run`,
  and Python caches.
- No production WordPress requests or update operations were made for this fix. Real Mac
  Desktop rendering remains unverified; its manually installed client file must be replaced.

## Original 0.1.0 verification (historical)

- Joined Python core → real connector → real WordPress **7.1.4 / PHP 8.3.36**, using a freshly booted, disposable Playground. `scripts/record_fixtures.py --creds-file .e2e-run/creds.json --apply plugin:classic-editor/classic-editor.php --apply theme:twentytwentyfour --content --out tests/fixtures/recorded.json` completed **13/13 steps**.
- Classic Editor **1.6.3 → 1.7.0** and Twenty Twenty-Four **1.0 → 1.6** were actually installed. Independent core REST reads confirmed installed versions and retained active state. Post-update homepage/REST/connector health verdicts were **ok** for both runs; rescans succeeded.
- Created a draft post, fetched it, edited both its title and Gutenberg body, then independently fetched the exact post and asserted edited title/body and **draft** status. No publishing occurred.
- Recorded **13 endpoints / 28 responses**, with no request credentials; real write/read-back fixtures replace the earlier read-only recording.
- `cd tests && /opt/hermes/.venv/bin/python -m unittest`: **119 tests passed**. Includes strict SDK-stub Desktop rendering (not the real application), API router tests, approval/privacy regressions, resolved-home-only `.env` writing, and two new joined-contract regressions.
- `node --check desktop/plugin.js`: passed.
- PHP-wasm parse and actual `php -l`: **10/10 files passed on each of PHP 8.3.36 and PHP 7.4.34-dev**. This is syntax verification, not PHPCS/WPCS certification.
- Hermes `plugins validate --install-deps` and `plugins doctor --ci` passed on a clean copy excluding `.research`, `.e2e-run`, `.git` and Python caches. Security scan **safe**, SDK surface clean, **9 tools / 1 hook** registered.
- Checked only key names in `/opt/data/.env`: **no WPM_* keys remain**. Test credentials stayed in gitignored harness/private scratch data, not the live Hermes configuration.
- Playground stopped; TCP port **9400 closed** after cleanup. No production sites, live plugin install/configuration changes, restarts, public release, or submissions.

## Changes and safety review

- Hardened fixture recording: `ok:false`, `success:false`, or non-ok health now fail a step rather than being marked successful merely because no exception occurred. Plugin/theme runs must show a real version change, independently verified through core REST, with active state preserved. Draft edits require exact read-back.
- Corrected verifier integration parsing: core theme REST returns `version` as a string here; tolerate either string or `{raw:...}`.
- Hardened update normalization: operation-level failure/partial/unknown results and global errors cannot be masked by successful per-item results. Added regression coverage.
- Reviewed approval gating, cron/unattended behavior, credential binding, same-origin authenticated redirects, home-targeted env writing, backend route reuse, connector per-type capabilities, major-core policy, and atomic update-lock acquisition/finally release. These checks are not a comprehensive security audit.
- Root license is MIT; bundled WordPress connector is GPLv2-or-later.

## Limitations and remaining gates

- **Real Hermes Desktop UI was not tested.** Node syntax, strict SDK stubs, surface scans and FastAPI router tests do not establish actual Desktop compatibility or end-to-end download behavior.
- Host isolation buffers backend calls and can return **504 after 60 seconds** while a longer update/scan continues. No async job/poll implementation yet. Do not retry update POSTs blindly after a timeout; scan and check health first.
- This joined run exercised plugin/theme updates and draft content. Minimum-WP, core update, Abilities, translation and admin-connection checks remain the connector worker's recorded results in `e2e/CONNECTOR_REPORT.md`, not newly repeated here. The recorder's independent version verification currently supports plugin/theme updates only.
- No backups, rollback, staging, malware scanning, live vault integration, MCP Adapter test, or production validation. Page-builder detection is heuristic; externally stored builder data may not be exposed in REST.
- Optional event imports can warn when tests are launched at repository root because root `tools.py` shadows Hermes's `tools` package. The specified `tests/` invocation passed without those warnings; polling remains available.
- `.research` contains upstream documentation that triggers scanner findings; it is ignored and excluded from the validated/shipped tree. Validate a clean checkout/package.
- Catalog acceptance of direct `op read` / fallback `.env` storage still needs review. Repository remains private; live installation, publication and submissions require separate authorization.

The earlier worker reports are historical build records; the verified joined-write results above supersede their read-only integration gaps.
