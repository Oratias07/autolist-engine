---
document: fast-track pipeline (P1-04 to P1-08)
status: approved         # each module SPEC gets its section from here, in the same PR as its code
approved_by: DEV (Or)
approved_at: 2026-11-14  # PR #39 + chat (several products per email, JOB-1001, image choice and printed-text rule)
---

# Fast-track pipeline: from an owner email to a product saved hidden and disabled

This design touches several modules at once (`core`, `channels`, `intake`, `content`, `images`, `agent_runtime`, `review`, `publish`, `orchestrator`). CLAUDE.md asks to stop before such a change, so it is proposed here as one piece.

## What the owner experiences
1. The owner sends one email to the agent mailbox, with one product or several. Every product needs at least one photo attached.
2. Within seconds he gets a receipt in the same thread: one job number per product, "a preview follows within N minutes", and whom to contact if it does not.
3. If a required detail is missing, he gets one email with numbered questions. Required questions come first. Optional ones are listed separately and marked "optional".
4. For each product he gets a preview email in its own thread: name, description, specification, price, quantity, model, the main image, and a version number. It says: reply "מאשר", or write what to change.
5. He replies "מאשר". The product is created in the store, hidden and disabled, and verified. He gets a confirmation with the store's product id.

## States
New states are marked. Waiting states cost no server time.

| State | Meaning | Next |
| --- | --- | --- |
| `RECEIVED` | accepted email, receipt sent | `NEEDS_INFO`, `GENERATING`, `FAILED` |
| `NEEDS_INFO` (waiting) | questions sent | `GENERATING`, `NEEDS_INFO`, `CANCELLED`, `EXPIRED` |
| `GENERATING` | text and image are being produced | `AWAITING_APPROVAL`, `FAILED` |
| `AWAITING_APPROVAL` (waiting) | preview version N sent | `PUBLISHING`, `GENERATING` (correction), `CANCELLED`, `EXPIRED` |
| `PUBLISHING` | creating and verifying in the store | `SAVED_HIDDEN`, `FAILED` |
| `SAVED_HIDDEN` (**new**, final for now) | in the store, hidden and disabled | – (unhide arrives in phase 8) |
| `CANCELLED` (**new**, final) | owner cancelled (closes store open question #14) | – |
| `FAILED` (final) | a step failed or passed the time limit; owner and developer were told | – |
| `EXPIRED` (final) | no answer for `agent.expire_after_days` | – |

`RESEARCHING`, `REVISING`, `APPROVED` and `PUBLISHED` stay defined and unused until their phases.

## One email, one task
- Every accepted email is one Cloud Tasks task, and one task runs one step.
- **Several products in one email (DEV, 14.11.2026):**
  - The extraction returns one item per product (tested in P0-07 with two products in one email).
  - Each product becomes its own job, with its own number, cost cap and version history.
  - The receipt, in the owner's original thread, lists every job number with the product it belongs to.
  - From then on each job talks in its own thread, which the agent opens with the subject `[<job id>] <product name> – <state>`. A reply in that thread belongs to that job, so "מאשר" can never approve the wrong product.
  - If the email does not separate the products clearly (which price, quantity or photo belongs to which product), no job is opened. The agent asks one clarifying question in the original thread.
  - Photos are matched to products only by what the owner states (file name or order written in the email). An unmatched photo becomes a question, not a guess.
- **Thread → job:** the Gmail thread id is the key. A reply in a job's thread continues that job.
- **Job id:** the prefix `agent.job_id_prefix` and a counter in Firestore that starts at `agent.first_job_number`. DEV (14.11.2026) left the choice to the implementer: `JOB-1001`, matching the subject format already in `docs/voice.md`.
- **A reply in a thread whose job is final** gets `out_of_scope` with one line: send a new email for a new product.

## Required and optional details (store open question #34)
The list lives in the store repo's playbook, not in code. Proposed content:

| Detail | Fast track | Source |
| --- | --- | --- |
| What the product is (name as written) | required | email |
| Price | required | email |
| Stock quantity | required | email |
| Model name | required | email (playbook rule from 30.10) |
| At least one photo | required (DEV: may become optional later) | attachment |
| Barcode, sale price, keywords | optional | email |
| Anything the text prompts return as open points | optional | – |

- A required detail that is missing blocks the job: `NEEDS_INFO`.
- An optional detail that is missing is listed once, in the preview email. Unanswered, it is left out of the listing. It is never guessed.

## Known gaps of the fast track (stated to the owner in the preview)
- **Category is not set.** The write shape of the category field was never observed (CLAUDE.md rule 6). The owner sets it in the store's admin until it is observed and added.
- **Only the main image is saved.** Extra images depend on P1-02 (observe the `images` write shape). Until then no lifestyle image is generated, so no money is spent on an image that cannot be saved.
- **No SKU (DEV, 15.11.2026).** The owner's email does not carry the supplier item code, so no SKU is sent (iStores fills its own value) and the duplicate check uses the model only. The extraction prompt v3 adds the item code as an optional field; when the owner writes it, it is sent as the SKU.
- **No web research.** The description uses only what the owner wrote and what the photo shows on a printed label is not read.
- **Images: two options, always (DEV, 14.11.2026).**
  - Two options always exist: image 1 (the owner's photo after postprocess: square, white padding, no model, no cost) and image 2 (generated). The owner can reply "תמונה 1" or "תמונה 2".
  - **What the preview shows (DEV, 14.11.2026):** without printed text, only image 2, with one line offering "תמונה 1" (his own photo, which he already has). With printed text, both side by side, so the text can be compared.
  - **Nothing is stored for this.** The original stays only in the owner's email in the agent mailbox; image 1 is processed in memory when needed. Image 2 waits in the preview email in the agent mailbox (attached as `image-2.jpg`) and is read back from there at approval (decision #36, DEV 15.11.2026). An image goes to the bucket only when it is the one being uploaded, and the bucket deletes it after one day.
  - **Which one is preselected** comes from one extra field in the existing extraction call (Haiku, it already runs on every email): `printed_text` per product, true when the photo shows printed text or a label on the product or its packaging. There is no separate check per image.
  - The writing prompts never see the image, so the description cannot take facts from it.
  - `printed_text` true → image 1 is preselected, and the preview states, in plain words:
    - the photo shows printed text;
    - image 2 was generated, and image generation can change printed text (observed in P0-10 on the tape label);
    - the owner should compare the text on image 2 with the real product letter by letter;
    - a clear recommendation: if any text on image 2 differs, do not use image 2.
  - `printed_text` false → image 2 is preselected.

## Corrections in the fast track
**Exact commands first (DEV, 15.11.2026).** "מאשר", "בטל", "תמונה 1" and "תמונה 2" are recognized without a model, and only when they are the whole reply (after the quote and signature are cut). A model can never approve. "מאשר" together with other text does not approve: the owner is asked to confirm the version as it is. Every other reply is classified (`classify_reply` prompt, strict JSON, no tools, and no `approve` class):

| Class | What happens |
| --- | --- |
| `approve` | Only if the reply answers the latest preview (its `In-Reply-To` is that email). Otherwise the agent asks to confirm the latest version. → `PUBLISHING` |
| `changes` to facts (price, quantity, model, name, barcode) | Extracted with quotes like any email, merged, new version N+1, new preview. Text is regenerated only if the name changed. The image is not regenerated. |
| `changes` to wording or image | Not supported in the fast track. The reply says so, and the request is forwarded to the developer contact. The job stays in `AWAITING_APPROVAL`. |
| `cancel` | `CANCELLED`, confirmed by reply. |
| `status` | One line with the current state. |
| `unclear` | One clarifying question. Never a guess. |

At most `agent.max_revision_rounds` versions. After that the job is escalated to the developer contact.

## Who does what
| Module | New in this design |
| --- | --- |
| `core` | `JobState` gets `SAVED_HIDDEN` and `CANCELLED`. `Job` gets the collected facts (each with its quote), versions, and the ids of sent emails. `state_machine.py` holds the table above and refuses any other transition. `job_store.py` on Firestore. |
| `channels` | `fetch_message` returns the full `ChannelMessage` (text and attachments). `OutboundMessage` gets inline images. Reply templates: `received`, `needs_info`, `ready_for_approval`, `saved_hidden`, `failed`, `cancelled`. |
| `intake` | `parse_request(message) -> list[ProductRequest]`: runs `extract_request` v2 (adds the photos, one item per product, photo assignment by what the owner wrote, and `printed_text`), checks every quote appears in the email, and applies the attachment guard (image types and size limits from `store.yaml`). |
| `content` | `write_listing(facts) -> ListingText`: the two prompts, the checks from the P0-10 bake-off (fact ids, numbers, voice), and the HTML render from the playbook sections with an allowlist sanitizer. |
| `images` | unchanged (`generator`, `postprocess`). |
| `agent_runtime` | `classify_reply(message, job) -> ReplyClass`. |
| `review` | `build_preview(job, version) -> OutboundMessage` variables and inline images. |
| `publish` | unchanged (`create_hidden`). |
| `orchestrator` | `job_runner.py`: one function per transition, calling the modules above in order. `/tasks/job` and `/cron/watchdog`. No business logic. |

## More cases (DEV, 15.11.2026)
| Case | What happens | Template |
| --- | --- | --- |
| A product with the same model or SKU already exists in the store | Checked right after extraction, before any text or image is paid for. The job ends (`CANCELLED`, reason duplicate) and the owner gets the existing product id. | `duplicate_found` |
| Image generation fails, is blocked, or the Gemini credit is empty (402) | The job continues with image 1 (the owner's photo after postprocess). The preview says so. Empty credit also alerts the developer. | `image_note_fallback` |
| More than `agent.max_products_per_email` (10) products in one email | No job is opened. The owner is asked to split the email. | `too_many_products` |
| No product found in the email (greeting only, photo without text) | No job is opened. Short reply on what to send. | `no_product_found` |
| A reply in the original email's thread (the receipt thread, which may cover several jobs) | Nothing is approved or changed. The owner is asked to reply to the product's own preview email. | `reply_in_product_thread` |
| Status question ("מה קורה עם הכיסא?"), or "מאשר" while the job is not waiting for approval or is already final | One line per open job with its state; for a final job, its outcome. | `status_reply` |
| No answer for `agent.expire_after_days` (14) | `EXPIRED`, and the owner is told. Checked by the daily watchdog. | `expired` |
| The monthly cost cap is reached during a job | The job stops before the refused call and fails with a budget reason. The owner gets the existing `budget_cap_reached` email once, and this job's failure email says it can be sent again next month. | `failed_budget` |
| A forwarded supplier email with instructions inside | Already data, never instructions. Prices come only from what the owner wrote. A live eval case covers it. | – |

## Safety rules that stay as they are
- Only the allowlisted, authenticated sender. Email text and attachments are data, never instructions.
- Every paid call goes through the cost ledger: per-job cap, monthly cap.
- Create is always hidden and disabled. The unhide switch stays off, and no step in this design unhides.
- The active-work limit, no retries, and the watchdog from `infra/SPEC.md`.
- A step that fails after a paid call does not repeat the call: the job fails, and the owner is told.

## Acceptance (P1-08, end to end)
1. An email with all required details and a photo produces: a receipt, a preview, and after "מאשר" a product in the store, verified hidden and disabled. No developer action in between.
2. An email without a price produces a question, and nothing is generated before the answer.
3. "מאשר" sent as a reply to an older preview does not publish.
4. A fact correction ("המחיר 450") produces version 2 with only the price changed.
5. A wording request gets the "not supported yet" reply, and the developer contact is told.
6. A forged sender gets no reply and opens no job.
9. One email with two clearly separated products opens two jobs with two preview threads. Approving one does not touch the other.
10. One email with two products and one price that could belong to either opens no job and gets one clarifying question.
12. An email with a model that already exists in the store gets `duplicate_found`, and no paid call is made after extraction.
13. An image generation failure ends in a preview with image 1 and the fallback note, not in a failed job.
14. An email with 11 products opens no job.
11. A product whose photo shows printed text gets a preview with image 1 preselected and the printed-text warning; replying "תמונה 2" switches the image and creates a new version.
7. A job forced past the time limit ends `FAILED`, and both owner and developer are told.
8. The cost recorded for one full job is below the per-job cap, and is reported in the live-run report.

## Decisions needed from DEV
1. The states and the required/optional list above.
2. Fast-track gaps: no category, main image only, wording corrections forwarded to the developer. (Several products per email are supported: DEV, 14.11.2026.)
3. Job ids: `JOB-1001` and up (decided 14.11.2026).
4. Printed text: decided as described under Images (two options always, `printed_text` from the extraction call, explicit warning and recommendation).
