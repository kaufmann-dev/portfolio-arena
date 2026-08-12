# Rebuilt mobile ranking horizontal overflow

- Fixed: 2026-08-12 18:46:14 UTC (+0000)
- Commit before fix: `0f338c6389e314169d8a94c31723bc8b55070a5b`

## Symptom

At phone widths, rebuilt portfolio ranking cards extended past the right edge of the viewport and
allowed the whole page to be zoomed or scrolled horizontally. Managed portfolio rankings remained
within the viewport.

## Confirmed root cause

The mobile ranking list used an implicit CSS Grid column with the default `auto` minimum. The
rebuilt cards had a wider min-content size than the managed cards, so the implicit track expanded
beyond its container. At a 393px viewport, the 369px ranking list produced a 467px grid track and a
479px document scroll width.

## Changes

- Define the mobile ranking list's single column as `minmax(0, 1fr)` so card content can shrink to
  the available width.
- Add a design regression test that requires the explicit shrinkable mobile grid track.
