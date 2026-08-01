# Evaluation runs table cell background gaps

- Fixed: 2026-08-01 13:23:39 UTC (+0000)
- Commit before fix: `7d77a75fd9acdaa89768708f43b204ba7ed9006c`

## Symptom

Queued evaluation rows showed rectangular white gaps in the Finished column instead of a continuous
row background.

## Confirmed root cause

The Finished table cell directly used the `cell-line` truncation class. That class sets
`display: block`, which replaced the cell's native `table-cell` display and removed it from normal
table layout. The cell background consequently covered only the text-height block instead of the
full row height.

## Changes

- Keep the Finished value's `td` as a native table cell and apply truncation to an inner span.
- Add a design regression test that rejects `cell-line` on evaluator table cells.
