# Grouped market refresh starvation

- Confirmed: 2026-08-17 21:15:12 UTC (+0000)
- Pre-fix commit: `c93d98fa71943bb51ffa64c5b8f59dac04a5d30f`

## Symptoms

- The production watermark remained at `as_of=2026-08-14` and
  `target_as_of=2026-08-17` long after the 2026-08-17 close and publication SLA.
- Portfolio Arena marked every rebuilt row as `stale data` even though Massive already returned
  2026-08-17 daily closes for SPY and sampled portfolio symbols.
- Repeated reads of `/api/market-data` did not advance the watermark, proving this was a pinned
  background publication job rather than a browser cache problem.

## Root causes

- Production had 489 distinct rebuilt symbols. The refresher sent one aggregate request and one
  dividend request per due symbol, so a newly closed session expanded into roughly 1,000 provider
  calls behind a 16-worker pool.
- The refresher waited for the entire unbounded per-symbol job before committing any successes.
  Rate limits and per-symbol deadlines could therefore starve SPY and the current holdings behind
  irrelevant historical maintenance work.
- Rebuilt row warnings required every symbol ever signaled to print on every later SPY session.
  That extended a signal's data-quality window indefinitely even though rebuilt cohorts expire
  after at most 20 close-to-close intervals.

## Fix

- Fetch each recent session through Massive's grouped U.S. stock endpoint, plus one all-market
  dividend request and one all-market split request. This refreshes the live 489-symbol set with
  three provider request sequences instead of roughly 1,000 per-ticker requests.
- Apply same-session dividend and split history factors to the cached total-return series before
  appending the new close, then publish all available grouped closes in one database transaction.
- Keep the per-ticker history repair path as a bounded 16-symbol fallback, prioritizing SPY and
  active symbols and committing successful progress on every pass.
- Define rebuilt readiness and row-level stale/frozen warnings from the actual H1-H20 signal
  lifecycle. Completed cohorts no longer require current prints from symbols they stopped using.
- Add regression coverage for a 489-symbol one-batch refresh, partial progress, provider fallback,
  corporate-action adjustment, and completed-cohort warning scope.
