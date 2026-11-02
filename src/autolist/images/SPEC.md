---
module: images
status: proposed         # P0-10 part only: generator.py + postprocess.py. The rest (classifier, budget, text_guard) is written in phase 6.
proposed_at: 2026-11-02
phase: 0 (generator + postprocess for the P0-10 bake-off), 6 (everything else)
---

# images

## Purpose
Turns reference images into store images: a main image on white and a lifestyle image, faithful to the real product. It also classifies reference images, applies the packaging-text guard and enforces the image budget (phase 6).

**P0-10 scope (this proposal):** `generator.py` (one image-model call) and `postprocess.py` (square, resize, re-encode). Nothing else in this module is written yet.

## Planned files
- `generator.py`, `postprocess.py` (P0-10)
- `classifier.py`, `budget.py`, `text_guard.py` (phase 6)

## Inputs
`ImageGenerator.generate(req: ImageRequest) -> GeneratedImage`
- `ImageRequest` (core model):
  - `job_id`
  - `model`: always explicit. Production passes `images.model`; the bake-off passes each id from `images.bakeoff_candidates`.
  - `role`: `"main"` or `"lifestyle"`.
  - `prompt_name`: store-repo prompt, `image_main` or `image_lifestyle`.
  - `variables`: prompt variables.
  - `references`: 1–4 reference images (`bytes` + mime type, from the store repo or Cloud Storage).
- The prompt is loaded by `llm.prompt_loader`. Product text from invoices or emails goes into the prompt as untrusted data, never as instructions.

`postprocess(image: bytes, output_px: int) -> bytes`
- Pads to a square on white, never crops the product.
- Resizes to `images.output_px` (1200).
- Strips EXIF and re-encodes as JPEG.

## Outputs
`GeneratedImage` (core model):
- `model`
- `role`
- `mime_type`
- `data: bytes` (raw model output, before postprocess)
- `size_px` (width × height as decoded)
- `text_parts`: any text the model returned. It is kept for the review page and is never used as product content.

## Behaviour
1. **Before the call:**
   - `cost_ledger.require(job_id, "gemini", "image:<role>", est)`.
   - `est` = `images.prices.<model>.per_image_usd.<generate_size>` + input estimate.
   - A model or size without a price raises `ConfigMissing`. There is no default price.
2. **One HTTPS `generateContent` call** with the prompt, the reference images inline, and the image size from `images.generate_size` ("2K", the smallest documented size ≥ 1200 px).
3. **Reading the image:**
   - The image is read from the response shape documented by Google: `candidates[0].content.parts[].inlineData{mimeType, data}`.
   - Per CLAUDE.md rule 6, the first live call records the real response shape (redacted, without image bytes) in the store repo. Until that record exists, the bake-off is the only caller.
   - A response without an image part raises `ImageNotReturned`, with the model's text and finish reason. No retry.
4. **After the call:**
   - `cost_ledger.record(CostEntry(category="image", provider="gemini", ...))`.
   - The price is the per-image price from `store.yaml` plus input tokens from `usageMetadata`, if present.
   - Missing usage data does not invent a number. The entry is recorded with the documented per-image price only, and `estimated=True`.

## Side effects
One HTTPS call per attempt, and one cost ledger entry per returned image. No storage writes; the caller decides where images go.

## Errors
| Error | When | Retry |
| --- | --- | --- |
| `CostCapExceeded` (core) | ledger refuses | no call made |
| `BillingRequired` | HTTP 402 (prepay balance is 0) | no. Message: owner must add prepay credit |
| `ImageRateLimited` → `ImageUnavailable` | 429, 5xx | backoff, at most 3 tries, honors `Retry-After` |
| `ImageRequestRejected` | other 4xx, or blocked by safety | no |
| `ImageNotReturned` | 200 without an image part | no |

The API key is read with `core.config.get_secret(cfg, "gemini_api_key")` and sent only in the `x-goog-api-key` header. It is never logged.

## Allowed dependencies
`autolist.core`, `autolist.llm` (prompt loader only), `httpx`, `Pillow`.

## Not responsible for
- Choosing which reference image is the product photo (`classifier`, phase 6).
- Judging whether the output is faithful to the product, including printed packaging text. In P0-10 that is checked by people on the review page; `text_guard` comes in phase 6.
- Uploading or publishing images.

## Core amendment needed (proposed together with this SPEC)
`ImagesConfig` in `core/models.py`:
- **Removed:** `price_per_image_usd`.
- **Added (all required; null = ask):**
  - `generate_size: str`
  - `bakeoff_candidates: list[str]`
  - `prices: dict[model_id, {input_usd_per_mtok, per_image_usd: {size: usd}}]`
- **New core models:** `ImageRequest`, `GeneratedImage`.

## Acceptance criteria (P0-10)
1. **Unit tests** (synthetic responses, no network):
   - a refused authorization sends zero requests
   - 402 → `BillingRequired`
   - 429 with `Retry-After` is honored, at most 3 tries
   - 200 with no image part → `ImageNotReturned`
   - the cost entry has `category="image"` and the configured price
   - the key never appears in logs
2. **Postprocess:** a 900×1600 input becomes 1200×1200 with the product fully inside (padding, not cropping), and no EXIF.
3. **Live** (through the `live` workflow, within the phase-0 Gemini budget of 3 USD): one main image per bake-off product per candidate model, 2 variations. The report records the redacted response shape and the actual ledger spend.
