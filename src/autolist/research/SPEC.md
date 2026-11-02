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

## Known constraints (from live discovery, 2026-11-02)
- **Store SKUs can be meaningless.** iStores fills an empty SKU with a 32-hex value (155 of 573 products). Duplicate check: ignore SKUs matching `^[0-9a-f]{32}$`. Compare on real SKU, UPC/barcode (rarely filled: 7 of 573) and normalized name.
- **Model** is auto-filled (slug or hash) when empty, so it is not an identity signal.
- Catalog list: `GET /products/{limit}/{page}`, key `products`, 100 per page. Fields per item are in the store repo's field map.
