# Header control height and local Inter fallback

- Fixed: 2026-07-27 09:45:05 UTC (+0000)
- Commit before fix: `cf21afc5317cb623c05edc2a081db38c6311db6e`

## Symptom

The desktop theme button appeared taller than the account-menu trigger when its hover border became
visible. The interface also used Inter only for visitors who already had the font installed locally.

## Confirmed root cause

The theme button had an explicit 44px height while the account trigger had only a 40px minimum
height. Its transparent border hid the mismatch until hover changed the border color. The global
font stack named Inter but did not import a web font or bundle any font files, so browsers without a
local Inter installation used the next system-font fallback.

## Changes

- Both desktop header controls now use an explicit 44px height.
- The Inter variable font package is bundled with the frontend and imported before application
  styles, including normal and italic faces.
- The global sans-serif stack now selects the bundled `Inter Variable` family first.
