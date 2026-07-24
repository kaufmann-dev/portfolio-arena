# Evaluator waits for entire concurrency batch

- Fixed: 2026-07-24 23:12:52 CEST (+0200)
- Commit before fix: `52b9e2d7600566acd05651e68f3c811592c4d01c`

## Symptom

With evaluator concurrency set to five, the worker claimed and ran five portfolios but did not
claim more work when only some of those evaluations finished. The next queued portfolios started
only after all five evaluations completed.

## Confirmed root cause

The scheduler passed the entire claimed batch to `asyncio.gather` and awaited it before returning to
the claim loop. A runtime reproduction released two of five mocked evaluations and observed that
the worker had still made only one claim and reported all five runs as active.

## Changes

- The scheduler now owns a set of active evaluation tasks and waits for the first task to finish.
- Completed tasks are removed and the worker immediately claims replacements for the freed slots.
- The heartbeat active-run count follows the live task set.
- Scheduler shutdown cancels and joins all tasks it owns.
- A regression test verifies that two replacements start while three original evaluations continue.
