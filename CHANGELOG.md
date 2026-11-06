# Changelog

## 0.1.2

- Add explicit Desktop connection-string storage choice: create a new 1Password item in a
  selected vault (recommended when available), use an existing secret/reference, or opt into .env.
- Enumerate vault metadata only through configured `op_binary`; send item JSON on stdin, never
  secret argv. Require ID-based read-back equality and WordPress authentication before registry
  commit. Fail closed without .env fallback; preserve existing sites and vault items.
- Report actual storage after connection. CLI/auth unavailable states offer Retry and explicit
  alternatives. Failed creation/verification may leave a new vault item for manual removal.
- Add swapped-subprocess privacy, ordering, failure and Desktop storage regression coverage.

## 0.1.1

- Opening Desktop site details automatically loads previously missing pending-update records
  through the existing scan endpoint with `refresh: false`. Connection-summary counts stay
  visible while loading; list and detail then read the same cached scan.
- Missing, loading, failed and successfully empty detail lists are separate states. Failed
  queries stop interval retries and offer Retry. Rescan still requests `refresh: true` and now
  surfaces per-site `ok: false` failures.
- Disable update controls without a usable list; reject empty or stale confirmation batches
  instead of reporting a no-op success. Health checking remains separate.
- Add deterministic deferred-query/render regressions and a read-only backend contract test.
