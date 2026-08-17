# Massive delayed aggregate rejected

- Confirmed: 2026-08-17 21:48:09 UTC (+0000)
- Pre-fix commit: `5ccbe8efa17cb7195f0703d01f6ac23a8158f866`

## Symptoms

- The deployed grouped refresher completed all three Massive requests with HTTP 200 responses,
  but production still reported `as_of=2026-08-14`, `target_as_of=2026-08-17`, and
  `market_data_status=stale`.
- Application logs classified the grouped response and every capped per-ticker fallback response
  as `MassiveMalformedResponse`, so no 2026-08-17 prices reached the cache.
- Direct inspection showed valid adjusted bars, including SPY's 2026-08-17 close, inside envelopes
  whose provider status was `DELAYED`.

## Root causes

- The shared Massive response parser accepted only a top-level status of `OK`. Massive uses
  `DELAYED` for valid aggregate responses under the production data entitlement, so the parser
  rejected the envelope before validating or reading any result rows.
- Tests modeled successful responses only with `OK`, leaving the production entitlement shape
  uncovered.
- The grouped path also treated one malformed requested bar or corporate-action record as a
  batch-wide failure, allowing an isolated provider record to block every otherwise valid symbol.

## Fix

- Accept both documented successful result shapes observed from the configured entitlement,
  `OK` and `DELAYED`, while retaining strict validation for the results list, adjusted-price flag,
  timestamps, and closes.
- Skip malformed grouped bars by symbol and exclude only symbols with malformed same-session
  corporate actions; preserve every other valid symbol in the grouped publication.
- Add regression coverage using the exact `DELAYED` aggregate status and a batch containing one
  malformed requested symbol.
