---
document: fast-track pipeline (P1-04 to P1-08)
status: proposed         # one design for DEV approval; after approval each module SPEC gets its section from here, in the same PR as its code
proposed_at: 2026-11-14
---

# Fast-track pipeline: from an owner email to a product saved hidden and disabled

This design touches several modules at once (`core`, `channels`, `intake`, `content`, `images`, `agent_runtime`, `review`, `publish`, `orchestrator`). CLAUDE.md asks to stop before such a change, so it is proposed here as one piece.

## What the owner experiences
1. The owner sends one email to the agent mailbox: one product, with at least one photo attached.
2. Within seconds he gets a receipt: job number, "a preview follows within N minutes", and whom to contact if it does not.
3. If a required detail is missing, he gets one email with numbered questions. Required questions come first. Optional ones are listed separately and marked "optional".
4. He gets a preview email: name, description, specification, price, quantity, model, the main image, and a version number. It says: reply "מאשר", or write what to change.
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
- **Thread → job:** the Gmail thread id is the key. A new thread opens a job. A reply in a thread continues that thread's job.
- **Job number:** a counter in Firestore. The first number is `agent.first_job_number` in `store.yaml` (to be given by DEV; not guessed).
- **A reply in a thread whose job is final** gets `out_of_scope` with one line: send a new email for a new product.

## Required and optional details (store open question #34)
The list lives in the store repo's playbook, not in code. Proposed content:

| Detail | Fast track | Source |
| --- | --- | --- |
| What the product is (name as written) | required | email |
| Price | required | email |
| Stock quantity | required | email |
| Model name | required | email (playbook rule from 30.10) |
| At least one photo | required | attachment |
| Barcode, sale price, keywords | optional | email |
| Anything the text prompts return as open points | optional | – |

- A required detail that is missing blocks the job: `NEEDS_INFO`.
- An optional detail that is missing is listed once, in the preview email. Unanswered, it is left out of the listing. It is never guessed.

## Known gaps of the fast track (stated to the owner in the preview)
- **Category is not set.** The write shape of the category field was never observed (CLAUDE.md rule 6). The owner sets it in the store's admin until it is observed and added.
- **Only the main image is saved.** Extra images depend on P1-02 (observe the `images` write shape). Until then no lifestyle image is generated, so no money is spent on an image that cannot be saved.
- **One product per email.** An email with several products gets a reply asking to send them one by one.
- **No web research.** The description uses only what the owner wrote and what the photo shows on a printed label is not read.
- **Packaging rule:** the owner marks in the email if the photo shows printed packaging (the playbook's packaging guard). Then the photo itself is processed (square, white padding) and no image is generated. Otherwise a main image is generated from the photo.

## Corrections in the fast track
The reply to a preview is classified (`classify_reply` prompt, strict JSON, no tools):

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
| `intake` | `parse_request(message) -> ProductRequest`: runs `extract_request`, checks every quote appears in the email, and applies the attachment guard (image types and size limits from `store.yaml`). |
| `content` | `write_listing(facts) -> ListingText`: the two prompts, the checks from the P0-10 bake-off (fact ids, numbers, voice), and the HTML render from the playbook sections with an allowlist sanitizer. |
| `images` | unchanged (`generator`, `postprocess`). |
| `agent_runtime` | `classify_reply(message, job) -> ReplyClass`. |
| `review` | `build_preview(job, version) -> OutboundMessage` variables and inline images. |
| `publish` | unchanged (`create_hidden`). |
| `orchestrator` | `job_runner.py`: one function per transition, calling the modules above in order. `/tasks/job` and `/cron/watchdog`. No business logic. |

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
7. A job forced past the time limit ends `FAILED`, and both owner and developer are told.
8. The cost recorded for one full job is below the per-job cap, and is reported in the live-run report.

## Decisions needed from DEV
1. The states and the required/optional list above.
2. Fast-track gaps: no category, main image only, one product per email, wording corrections forwarded to the developer.
3. `agent.first_job_number`.
4. The owner marks printed packaging in the email (a phrase to agree with the client), or every photo with packaging is treated as a product photo to regenerate from. The first is safer.
