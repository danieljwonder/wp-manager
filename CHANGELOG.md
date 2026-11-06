# Changelog

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
