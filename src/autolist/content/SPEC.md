---
module: content
status: approved         # fast-track scope only (below), via docs/fast-track-pipeline.md (DEV, 14.11.2026). The rest stays TBD for phase 5.
phase: 1 (fast track) / 5
---

# content

## Fast track (phase 1) — approved scope
Files: `writer.py`, `sanitizer.py`.

**Inputs.** `write_listing(facts, *, llm, playbook, glossary, voice, job_id)`:
- `facts`: the job's `JobFact` list. The latest fact per key wins (a reply can correct a fact).
- Only `name`, `model`, `barcode`, `keywords` are given to the writer, numbered `F1..` in that order. Price, sale price, quantity and the category hint never enter the text.
- `playbook.title`, `playbook.description` (sections), the glossary terms and the voice principles section of `docs/voice.md` (loaded by `core.playbook`).

**Outputs.** `ListingText` (core): `title`, `description_html` (sanitized), `description_text` (the same content as plain lines, for the preview), `open_points`, `problems`.

**Behavior.**
- Two calls on the `write` model: `write_title` (max 1000 output tokens) and `write_description` (max 6000), prompts v2 from the store repo. Strict JSON, no tools, no images.
- The model never writes HTML. The HTML is built in code from `description.sections` (headline, intro, specs, benefits), every model string escaped, then passed through `sanitize`. An unknown section id raises `ValueError` (the playbook changed without the code).
- Checks; each failure is a line in `problems`:
  - every cited fact id exists;
  - every number in the title or a spec value appears in one of its cited facts;
  - every number in the headline, intro or a benefit sentence appears in some fact;
  - no `!`, no emoji;
  - fewer than 3 benefits only together with an open point.

**Sanitizer.** Allowlist `p strong ul ol li br h2 h3`, no attributes. `script style iframe object embed template svg` are dropped with their content. Text is escaped; unclosed tags are closed.

**Side effects.** None besides the LLM calls (which go through the cost ledger in `llm`).

**Errors.** `LLMOutputInvalid`, `LLMUnavailable`, `CostCapReached` propagate. `TypeError` if a call returns no parsed result.

**Not responsible for.** Deciding what a problem means: the orchestrator does not send a preview with `problems` and alerts the developer. Category, attributes, SEO, pricing (phase 5).

**Acceptance criteria.** Tests in `tests/content/`: price and quantity never reach the prompts; each check fires; model strings cannot inject HTML; the sanitizer drops scripts, attributes and unknown tags.

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

## Known constraints (from live discovery, 2026-11-02)
- **Write explicitly.** Products created through the API get catalog flags off (`fb_feed`, `google_feed`, `tiktok_feed`, `zap` = 0). Anything the playbook wants on must be sent explicitly. Whether the flags are writable is being checked in P0-06b.
- **Related products:** the field the owner uses is `product_upsale` (list of product ids, writable).
- **Google product category:** `feed_category`. A string was not stored; the integer form is tested in P0-06b.
- **Language:** names and descriptions go under `product_description["3"]` (Hebrew, the only language used in the catalog).
