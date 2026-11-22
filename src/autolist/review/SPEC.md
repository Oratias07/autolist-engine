---
module: review
status: approved         # fast-track preview only (below), via docs/fast-track-pipeline.md (DEV, 14.11.2026). The rest stays TBD for phase 7.
phase: 1 (fast track) / 7
---

# review

## Fast track (phase 1) — approved scope
File: `preview.py`. Pure, no I/O.

**Inputs.** `build_preview(job, *, version, text, image_1, image_2, owner_choice, labels, to, in_reply_to)`:
- `text`: `ListingText` from `content`; `image_1`: the owner's photo after postprocess (JPEG); `image_2`: the generated image (JPEG) or `None` when generation failed, was blocked or had no credit.
- `owner_choice`: 1 or 2 after the owner replied "תמונה 1"/"תמונה 2", else `None`.
- `labels`: `core.playbook.load_field_labels()` (store repo `agent/field_labels.yaml`).

**Outputs.** `Preview` (core): an `OutboundMessage` for `ready_for_approval` (variables `job_id, product_name, version, title, price, quantity, model, description_text, optional_missing`), the image-note fragment name, the preselected image and the images attached in order (`image-1.jpg`, `image-2.jpg`). The orchestrator renders the fragment into `image_note` (review cannot import `channels`).

**Image rule (DEV, 14.11.2026).**
| Situation | Preselected | Attached | Note |
| --- | --- | --- | --- |
| no generated image | 1 | 1 | `image_note_fallback` |
| owner chose an image | his choice | 1, 2 | `image_note_owner_choice` |
| `printed_text` true | 1 | 1, 2 | `image_note_printed_text` |
| `printed_text` false | 2 | 2 | `image_note_generated` |

**Optional details shown.** Only those the fast-track listing uses: barcode, keywords. The writer's open points are not shown (P1-08, DEV 15.11.2026): the owner answered them, and the fast track could not use the answers. None missing → `labels["_none"]`. Sale price is extracted but not saved in the fast track (no observed write shape), so it is not listed.

**Errors.** `PreviewError` (never a guess): text with failed checks; price, quantity or model missing; `printed_text` unknown with a photo; image 2 chosen without a generated image.

**Not responsible for.** Sending (`channels`), storing the version (`orchestrator` via `core.job_store`), reply classification.

**Acceptance criteria.** `tests/review/test_preview.py`: each row of the image table; each `PreviewError`; latest facts win; the optional list and the `_none` label.

## Purpose
Builds the approval preview and batch digest, turns an owner reply into a structured patch (field + instruction), and issues/validates the HMAC approval token bound to job + version (single use, 72h).

## Planned files
preview.py, digest.py, reply_to_patch.py, approval_token.py

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
- **Prices shown as money (DEV 22.11.2026, stage A):** `shekels`: 7.90, 1,190, 1,190.50. A value that is not a number is shown as written. This is used for the price and the sale price in the preview.
