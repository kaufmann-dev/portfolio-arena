# Portfolio age and stale market warning

- Confirmed: 2026-07-29 20:38:30 UTC (+0000)
- Pre-fix commit: `fbfc1ecc79796bcd391bfab6073d6b609f5c28c8`

## Symptoms

- Portfolios that began on 2026-07-27 displayed an age of `1d` on 2026-07-29.
- A yellow warning appeared whenever the API reported usable last-known market data as `stale`,
  including the normal period after a market close.

## Root causes

- Portfolio age was calculated from the last valued market close (`as_of`) instead of the current
  New York calendar date. When `as_of` was 2026-07-28, a portfolio from 2026-07-27 therefore
  appeared one day old on 2026-07-29.
- The shared frontend warning helper treated both `stale` and `unavailable` data as warning
  conditions, even though stale cached prices remain complete and usable.

## Fix

- Carry the current New York date through arena valuation orchestration and calculate elapsed age
  from that date while retaining `as_of` as the authoritative valued close.
- Render no warning for `fresh` or usable `stale` data. Keep the warning for `unavailable` data,
  where valuations can genuinely be incomplete.
- Add backend and frontend regression tests for both behaviors.
