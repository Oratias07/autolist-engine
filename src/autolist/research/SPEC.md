---
module: research
status: not-started      # written by Claude Code at the start of phase 4, approved by DEV before code
phase: 4
---

# research

## Purpose
Duplicate check against the store catalog (SKU, barcode, normalized name), web search, page fetch with SSRF guard, JSON-LD extraction, and a match score per source. Competitor prices are kept for internal display only. Competitor images are never used for generation.

## Planned files
duplicate_check.py, search_provider.py, image_search.py, page_fetcher.py, jsonld_extractor.py, matcher.py

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
