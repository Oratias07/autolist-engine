---
module: intake
status: not-started      # written by Claude Code at the start of phase 3, approved by DEV before code
phase: 3
---

# intake

## Purpose
Turns a `ChannelMessage` into structured requests: a single-product request, or invoice rows (xls/xlsx/csv/pdf, with images per row). It strips sensitive invoice data (FOB cost, supplier name, bank details) before any LLM call. Missing fields stay None.

## Planned files
request_parser.py, invoice_parser.py, invoice_images.py, attachment_guard.py

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
