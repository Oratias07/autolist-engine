---
module: intake
status: approved         # fast-track scope (single email, one or more products), from docs/fast-track-pipeline.md (PR #39, DEV 14.11.2026). Invoices (xls/xlsx/csv/pdf) stay phase 3.
approved_by: DEV (Or)
approved_at: 2026-11-14
phase: 1 (fast track), 3 (invoices)
---

# intake

## Purpose
Turns a `ChannelMessage` from the owner into products with quoted facts. It also guards the attachments. Missing values stay missing: they are listed, never filled.

## Files
| File | Phase | Does |
| --- | --- | --- |
| `attachment_guard.py` | 1 | Decides which attachments are usable photos and prepares them. |
| `request_parser.py` | 1 | Runs `extract_request` v2 and checks its output. |
| `invoice_parser.py`, `invoice_images.py` | 3 | Supplier invoices. |

## Inputs
`parse_request(message, *, llm, intake, required, job_id) -> ParsedRequest`
- `message`: `ChannelMessage` (already sender-verified by `channels`).
- `intake`: `IntakeConfig` (`store.yaml` → `intake`).
- `required`: required detail keys from the playbook (`listing.required`). Not in code.

## Outputs
`ParsedRequest` (core):
- the products, one `ProductRequest` each: quoted facts, photo indexes, `printed_text`, missing required keys, and unverified fields
- whether the products are clearly separated
- unassigned photos
- the model's questions
- rejected attachments (name and reason)
- the accepted photos

## Security (end to end)
- **Email text, file names and image content are untrusted.** Text and names go into the prompt inside `<untrusted>` tags, photos as image blocks. The prompt states that text inside a photo is data.
- **The model's output is checked, not trusted:**
  - A value without an exact quote of the email text is dropped and reported in `unverified_fields`.
  - A photo index outside the accepted list is dropped.
  - Missing required details come from the playbook list, not from the model.
- **Attachments are checked by decoding, not by the declared MIME type:**
  - type, size and count limits come from `store.yaml`;
  - HEIC/HEIF photos (iPhone camera) are read through `pillow-heif` and re-encoded to JPEG like any other photo (DEV, 15.11.2026);
  - the pixel count is checked from the header before decoding (decompression bombs);
  - accepted photos are re-encoded, which strips EXIF and anything appended to the file, and downscaled to `intake.llm_image_max_edge_px`.
- **File names** are reduced to a display-only form: no path, no markup characters, at most 80 characters.
- **No reply text in code.** Questions about dropped or missing values are rendered from the store repo's `needs_info` template.
- **Cost:** the extraction call goes through the cost ledger. Image tokens are included in the estimate (`llm` SPEC).

## Side effects
None besides the LLM call (paid, through the ledger).

## Errors
`CostCapExceeded`, `LLMOutputInvalid` and `LLMUnavailable` from `llm`. A missing playbook key raises `ConfigMissing`.

## Allowed dependencies
`autolist.core`, `autolist.llm`, Pillow, pydantic.

## Not responsible for
- Deciding what happens with missing details (orchestrator).
- Wording questions to the owner (store repo templates).
- Fetching the email (`channels`).

## Acceptance criteria
**Unit (fake LLM, synthetic images):**
1. Quoted facts are kept.
2. An unquoted value is dropped and listed.
3. Missing required keys include "photo" when there is no photo.
4. Out-of-range photo indexes are dropped.
5. A declared image type that does not decode is refused.
6. The pixel limit is checked before decoding.
7. EXIF is stripped.
8. File names are sanitized.

**Live:** the P0-07 email samples plus new fast-track cases are re-run with v2 through the `live` workflow, within the phase-0 Anthropic budget. The cases are: two products with photos, a shared price that cannot be assigned, a photo with printed text, and an injection inside an image.
