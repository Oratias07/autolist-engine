---
module: images
status: not-started      # written by Claude Code at the start of phase 6, approved by DEV before code
phase: 6 (generator spike in P0-10)
---

# images

## Purpose
Classifies reference images (product / material / spec sheet / other), generates main (white background) and lifestyle images via the image model, post-processes them with Pillow (square, size from store.yaml, strip EXIF, re-encode), applies the packaging-text guard, and enforces the image budget.

## Planned files
classifier.py, generator.py, postprocess.py, budget.py, text_guard.py

## Inputs
TBD in this SPEC before code.

## Outputs
TBD.

## Side effects
TBD. Default: none (pure).

## Errors
TBD.

## Allowed dependencies
`autolist.core`, `autolist.llm`.

## Not responsible for
TBD.

## Acceptance criteria
Taken from the phase file in the store repo, then refined here.
