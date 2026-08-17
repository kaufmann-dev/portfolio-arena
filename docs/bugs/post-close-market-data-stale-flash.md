# Post-close market data stale flash

- Confirmed: 2026-08-17 20:37:58 UTC (+0000)
- Pre-fix commit: `85532bc196dc212d8fdfeee9d61bfe662b2050c0`

## Symptoms

- At 20:11 UTC, pages still showed the prior 2026-08-14 close even though the market had closed.
- At the configured 20:15 UTC provider cutoff, the same complete snapshot abruptly changed from
  `fresh` to `stale` before the newly closed daily bars had been published consistently.
- The first visitor after the cutoff could wait on Massive and then trigger a five-minute retry
  cooldown, extending the stale window even when the missing bars appeared seconds later.

## Root causes

- Valuation GET requests doubled as cache refresh workers. The first read after the fixed
  close-plus-15-minute cutoff synchronously fetched every due symbol.
- A response missing the target session was treated like a provider failure and entered the same
  300-second cooldown as a real transport or service failure.
- Price rows were accepted independently, so there was no publication boundary separating the
  prior complete session from a partially available new session.

## Fix

- Move Massive downloads into a lifespan-managed background refresher guarded by a PostgreSQL
  advisory lock. Public API and MCP valuation reads are now cache-only.
- Build the new target session off-path and publish all currently relevant symbols in one database
  transaction. Keep serving the prior coherent `as_of` while the batch is incomplete.
- Report normal provider lag as `updating` for 10 minutes and retry after 15, 30, then 60 seconds.
  Reserve `stale` for data still incomplete after that SLA and `unavailable` for missing history.
- Add `/api/market-data` as a lightweight polling endpoint. Updating pages automatically reload
  their data once the new close is published, without slowing the initial page request.
- Add regression coverage for cache-only reads, atomic batch withholding/publication, degraded
  fallback after the SLA, provider failures, the status endpoint, and updating UI copy.
