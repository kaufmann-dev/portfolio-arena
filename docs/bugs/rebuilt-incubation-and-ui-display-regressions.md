# Rebuilt incubation and UI display regressions

- Confirmed: 2026-08-01 10:10:41 UTC (+0000)
- Pre-fix commit: `e55522ad08625fc16ccd7c874c596538192077ff`

## Symptoms

- An incubating rebuilt portfolio with a recorded signal showed no aggregate holdings or active
  cohorts and reported `0 / 0` completion.
- A stale frontend bundle retained a half-width aggregate table and duplicate disclosure dividers.
- Floating-point drift rendered as red `-0.0%` beside neutral `0.0%` values.
- Signal history called locked signals complete, and performance charts included an unwanted range
  scrubber below the plot.

## Root causes

- Common-policy admission and the policy used to display the live aggregate book shared the same
  nullable selection. Before admission, serialization therefore discarded a valid H20/100 policy.
- The rebuilt analytics cache retained selected policies but not the H20 incubation policy.
- Percentage text rounded values while sign classes used their unrounded inputs.
- The public signal label described evaluation completion even though its boolean represented edit
  locking. The chart scrubber was a separate range input rather than part of the plot interaction.
- SPA and hashed asset responses had no explicit cache policy, allowing an old HTML entry point to
  continue referencing presentation assets from before the UI cleanup.

## Fix

- Expose a provisional H20/100 aggregate policy for active Common incubation details while keeping
  admission, evidence, ranking, and performance-series selection unchanged.
- Retain that policy in the analytics cache and use H20 statistics for incubation completion.
- Normalize percentages at their displayed precision before producing text or sign classes.
- Rename locked signal states, remove the chart scrubber, and preserve pointer inspection plus the
  textual chart summary.
- Revalidate the SPA shell on every request while caching Vite's hashed assets as immutable.
- Add API, cache-policy, and numeric-format regression tests and verify the affected layouts at
  desktop and mobile widths.
