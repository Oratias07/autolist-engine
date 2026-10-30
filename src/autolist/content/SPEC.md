---
module: content
status: not-started      # written by Claude Code at the start of phase 5, approved by DEV before code
phase: 5
---

# content

## Purpose
Builds the product draft from sourced facts and the playbook: title, description HTML, attributes, categories, stock, pricing, SEO. Includes the sanitizer, the style check (glossary, forbidden words, units) and the similarity check against sources. It also runs the numeric fact cross-check: every number traces to a source, and a conflict becomes a question.

## Planned files
title.py, description.py, attributes.py, categories.py, stock.py, pricing.py, seo.py, sanitizer.py, style_check.py, similarity_check.py, fact_check.py

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
