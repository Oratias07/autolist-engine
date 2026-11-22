---
module: orchestrator
status: approved         # P0-09 skeleton only (cost-alert delivery); the rest is written and approved at the start of phase 1
approved_by: DEV (Or)
approved_at: 2026-11-02  # PR #19 + chat, including the 4 decisions listed there
phase: 0 (P0-09 skeleton), 1 (everything else)
---

# orchestrator

## Purpose
The only package allowed to import feature modules. It wires them together: it receives channel events, drives the job state machine (core), calls feature modules in order, and hands results to channels and publish. It contains no business logic: no prompt text, no product rules, no field decisions, no cost arithmetic. Cloud Run entrypoints (Pub/Sub push, Cloud Tasks handlers, Scheduler jobs) live here.

**P0-09 scope (this proposal):** one file, `cost_alerts.py`. It drains the cost ledger's alert outbox and sends the owner one email per drain. Everything else in this SPEC stays TBD until phase 1.

## Planned files
- `cost_alerts.py` (P0-09)
- `orchestrator.py`, `entrypoints.py`, `tasks_queue.py` (phase 1)

## Inputs (P0-09)
`deliver_cost_alerts(ledger, channel, owner, open_jobs, now) -> list[str]`
- `ledger`: `core.cost_ledger.CostLedger`.
- `channel`: anything with `send(OutboundMessage) -> str` (a local `Sender` protocol; every `channels.base.Channel` satisfies it, and so does the send-only `GmailSender`).
- `owner`: the owner's address, from `store.yaml` (the allowlisted sender). Never a literal.
- `open_jobs`: an `OpenJobsCounter` protocol (`count() -> int`). Phase 0 has no job runner. Its implementation is `NoJobsYet`, which returns 0 because no job can exist yet. In phase 1 it is replaced by the job store.
- `now`: aware datetime (injectable for tests).

## Outputs
The message ids returned by the channel, in send order. An empty list when the outbox was empty.

## Behaviour (P0-09)
1. `events = ledger.drain_alerts()`. If empty, return `[]`.
2. If any event is `CostCapReached`, send **one** `budget_cap_reached` and nothing else for this drain. The cap email already says paid work stopped.
3. Otherwise, send **one** `cost_alert` for the highest threshold in the drain. A single spend that crosses 30 and 40 at once sends one email, not two.
4. Template variables come only from `ledger.month_summary(now)` (core) and `open_jobs.count()`. This module only formats them as strings: USD with 2 decimals, dates `DD.MM.YYYY`.

| template | variable | source |
| --- | --- | --- |
| `cost_alert` | `month_spend`, `monthly_cap` | summary |
| | `llm_spend`, `image_spend`, `search_spend` | summary, by `CostEntry.category` |
| | `jobs_count` | summary: distinct non-null `job_id` this month |
| | `next_threshold` | summary: the next threshold above spend; when none is left, `monthly_cap` (the next email is the cap email) |
| `budget_cap_reached` | `monthly_cap` | summary |
| | `reset_date` | summary: first day of next month, in `store.timezone` |
| | `open_jobs_count` | `open_jobs.count()` |

## Side effects
Only through `channel.send`. Draining the outbox is the ledger's own state change.

## Errors
- A send that fails puts the drained events back in the outbox (`ledger.requeue_alerts(events)`) and re-raises, so an alert is never lost silently.
- A template or variable error (`TemplateError`) is not caught. It is a bug in the store repo, and it must fail loudly.

## Allowed dependencies
`autolist.core`, `autolist.channels`.

## Not responsible for
- Deciding thresholds or the cap (core and `store.yaml`).
- Wording (store repo `agent/replies`).
- When to call it. In phase 0 the P0-09 spike calls it. In phase 1 the orchestrator calls it after every job step.

## Acceptance criteria (P0-09, from `tasks/phase-0.md`)
1. Ledger seeded to 29.90, then a 0.20 entry: exactly one `cost_alert` is sent to the owner, with month_spend 30.10, monthly_cap 60.00, next_threshold 40.00 and the correct split.
2. A second entry right after sends nothing.
3. A simulated entry that brings the month to 60.00 sends exactly one `budget_cap_reached`, and the next `authorize()` is refused.
4. A send failure leaves the events in the outbox (the next call sends them).
5. Live: the P0-09 spike runs once through the `live` workflow. It uses an in-memory ledger, makes no paid calls and does no store writes. It sends criteria 1 and 3 as real emails from the agent mailbox to the owner, and the report records the Gmail ids.

## Entrypoints (P1-03, behaviour approved in `infra/SPEC.md` on 2026-11-14)
Files: `entrypoints.py` (Starlette app, wiring only) and `tasks_queue.py` (Cloud Tasks).

| Route | Behaviour |
| --- | --- |
| `GET /health` | Returns the injected health report; 503 when it is not ok. |
| `POST /events/gmail` | Refuses a body that is not a Pub/Sub push (400). Otherwise calls `channels.email.inbox.poll_accepted` and enqueues one `/tasks/job` task per accepted message. |
| `POST /cron/renew-watch` | Calls `channels.email.inbox.renew_watch` and returns the new expiry. |

- **Where the logic lives:**
  - Reading the mailbox history, claiming message ids and verifying the sender are in `channels`.
  - The cursor and the claimed ids are in `core.state_store` (Firestore).
  - This module only connects them to the queue.
- **Duplicates:** a message id is claimed before anything else happens. A repeated push finds it claimed and enqueues nothing.
- **Rejected senders** are recorded with the reason and get no reply.
- **Not yet here:** `/tasks/job` and `/cron/watchdog` arrive with the job runner (P1-04). Until then the push subscription is not created, so no owner email is accepted and then left unanswered.
- **Authentication** is Cloud Run's: the service is not public, and only the invoker service account can call it.

## Job runner (P1-04, fast track; behaviour approved in `docs/fast-track-pipeline.md`, 14–15.11.2026)
Files, one step each, wiring only:
| File | What it wires |
| --- | --- |
| `runtime.py` | `Runtime` (every dependency, built once per process) and send helpers: record sent ids on the job, open the job's own thread on its first email, `developer_alert` with the detail redacted like a log line. |
| `intake_step.py` | A new owner email: receipt-thread reply → `reply_in_product_thread`; `parse_request`; no product → `no_product_found`; more than `agent.max_products_per_email` → `too_many_products`; unclear products, unassigned photos, or model questions with several products → `clarify_products` (no job). Otherwise one job per product, one `received` in the original thread, then per job: missing/unquoted required details or model questions → `NEEDS_INFO` + `needs_info` in the job's own thread; else the duplicate check, then `GENERATING` and a `generate` task. |
| `duplicate_check.py` | Before `GENERATING`: the store already has the model → `duplicate_found` in the original thread, `CANCELLED` (reason `duplicate model`). No paid call is made for it. |
| `generate_step.py` | `write_listing` (a failed check fails the job); the photos re-read from the owner's email and guarded again; image 1 = postprocess (free); image 2 = `image_main` on the job's photos, postprocessed. Any image error (402 included) → image 1 and a developer alert. `build_preview`, the image-note fragment, send, record the version with the preview's gmail id, `AWAITING_APPROVAL`. |
| `watchdog.py` | Active past `agent.job_step_timeout_s` → `FAILED` (`failed` + developer alert). Waiting with no change for `agent.expire_after_days` → `EXPIRED` + `expired`. |
| `job_runner.py` | `run_task(rt, payload)`: `email_received` and `generate`. A step error after a job exists fails the job: `failed`, or `failed_budget` when the monthly cap refused the call (`CostCapExceeded.monthly`), plus a developer alert. Nothing is retried. A reply in a job thread raises `ReplyHandlingNotReady` after a developer alert, until reply handling lands (next PR). |

Entrypoints: `POST /tasks/job` (400 on a body without `event`, 500 on a step error, logged) and `POST /cron/watchdog`.

Config used: `istores.catalog_page_size` (new, store.yaml; observed 100 in P0-05), `agent.*`, `images.model`, `images.output_px`.

Acceptance (tests in `tests/orchestrator/`): P1-08 cases 2, 9, 10, 12, 13, 14 and the no-product, receipt-thread, budget, watchdog and redaction cases. Cases 1, 3, 4, 5 need reply handling and publish (next PR); 6 is covered by `channels` and the entrypoint tests.

### Replies and publish (P1-07, decision DEV 15.11.2026)
| File | What it wires |
| --- | --- |
| `reply_step.py` | A reply in a job's own thread. Final job → `status_reply`. Exact commands (`agent_runtime.commands`): `approve` only in `AWAITING_APPROVAL` and only when In-Reply-To is the latest preview's Message-ID (an older preview → `approve_old_version`; no preview → `approve_reply_to_preview`), then `PUBLISHING` and a `publish` task; `cancel` from `NEEDS_INFO`/`AWAITING_APPROVAL` → `CANCELLED` + `cancelled`; `image_1`/`image_2` → a new version with the same text and both images (image 2 read back from the previous preview; none generated → `image_2_unavailable`), at most `agent.max_revision_rounds` new versions, then the developer. "מאשר" with other text → `approve_with_text`. A command in the wrong state → `status_reply`. Any other reply goes to `classify_reply`: `status` → `status_reply`; `other` → `out_of_scope`; `wording_or_image` → developer + `change_not_supported`; `unclear` → `reply_unclear` with the model's question; `facts` → `reply_facts.py`. |
| `reply_facts.py` | NEEDS_INFO: merge, ask again for what is missing, or the duplicate check and `GENERATING`. AWAITING_APPROVAL: merge and a new version; the text is written again only when a writer fact (name, model, barcode, keywords) changed; images are reused. A value without a quote is asked about, never used. |
| `new_version.py` | A new version with the previous images (image 2 from the previous preview), the owner's image choice kept, at most `agent.max_revision_rounds` new versions. |
| `publish_step.py` | The latest version: image 1 from the owner's email (guarded, postprocessed) or image 2 from the agent's own sent preview (`fetch_own_attachment`); to the bucket for the upload only, deleted in `finally`; `create_hidden` (duplicate check, hidden + disabled, verify); `SAVED_HIDDEN` + `saved_hidden`. No SKU (decision 15.11). A verify mismatch fails the job; the product stays hidden and disabled. |

Store-repo texts used: `agent/commands.yaml`, `agent/state_labels.yaml`, replies `approve_with_text`, `approve_reply_to_preview`, `image_2_unavailable` (drafts for DEV).

### Texts fail closed, cost alerts wired (DEV 15.11.2026)
- Prompts (`llm.prompt_loader`) and reply templates (`channels.templates`) are used only with `status: approved` or `approved_dev` (`core.text_status`). A draft, a missing status or `not-written` is refused. `AUTOLIST_ALLOW_DRAFTS=1` lets a live eval read a draft; `orchestrator.main` refuses to start while it is set.
- `job_runner.run_task` sends the cost alerts after every step, whether the step succeeds or fails: at most one `cost_alert`, or `budget_cap_reached` at the cap (`cost_alerts.deliver_cost_alerts`). If the send fails, the events go back to the ledger and are sent after the next step. The step itself is not failed.
- `/health` also loads every prompt and reply template the service uses (`main.PROMPTS_IN_USE`, `REPLIES_IN_USE`, the signature). A refused or missing text makes the deploy check fail, instead of the first owner email that needs it.


### P1-08 findings (first live session, 15.11.2026; DEV approved the fixes)
- **Approval binding:** "מאשר" approves the latest version when the reply answers any agent email of the same thread sent at or after the latest preview. It no longer has to be the preview email itself: the owner answers the last email he got. The check uses `channels.email.own_sent`: the answered message must be `SENT` from the agent mailbox, in the reply's thread, and its send time is compared with the previews' send times. A message sent before the latest preview → `approve_old_version` with the version the owner saw. No agent email matched → `approve_reply_to_preview`.
- **Open points:** the writer's open points are no longer shown to the owner (`review.preview`). He answered them and got "לא הובן", because the fast track cannot use those answers (a loop). Taking such details is the next change (owner details for the writer).
- **Quote headers:** a reply's quote header that Gmail wrapped over two lines is cut (`channels.email.reader`).

- **Owner details (DEV request 15.11.2026):** facts the owner states that have no field (size, material, use, what it does) are kept as `detail` facts, each with an exact quote (`core.facts.owner_details`), from the first email and from replies. The writer gets them as facts. A new detail in a reply rewrites the text, but not the images. The item code (`item_code`) is sent as the SKU and checked for duplicates (B-03). The prompt side is `extract_request` v3 and `classify_reply` v2 (drafts until DEV approves).
- **A NEEDS_INFO answer with nothing missing:** the open question was the model's own, for example the photo check (`extract_request` v3 rule 11a), and the reply answers it ("כן"). The job goes on to the duplicate check and `GENERATING`, instead of `reply_unclear`. The preview shows the photo, and the owner can still cancel.
- **An answer to a clarifying question (DEV 15.11.2026):** when the agent asked a question and opened no job (`clarify_products`, `no_product_found`, `too_many_products`), the owner's answer is read together with his earlier messages in that thread (`channels.email.thread_reader`, `intake_step.with_thread_context`). The texts are joined in order with a separator; the attachments follow in the same order, and so do the photo indexes. The job keeps every source message (`Job.more_source_gmail_ids`), so the photos are re-read the same way. Every message passes the sender, DKIM and DMARC checks again, and the agent's own messages are skipped. The owner never has to resend.
- **A model or an item code (DEV 15.11.2026, YTS-1002):** the playbook's required key `model_or_item_code` is met by either one (`core.facts.present_keys`). When there is no model, the item code fills the store's model field, the preview and the writer's model line (`core.facts.store_model`), and the duplicate check uses it. Only when both are missing is the owner asked, once.
- **Categories (DEV 15.11.2026):** `category_step.choose_for` reads the store list (read-only) and chooses when the text is written. The preview shows the names, or the `_category_none` fragment. A category the owner names in a reply (`category_hint`) chooses again, and the new version keeps the text and the images. The create sends `product_category` (observed in P1-02), and the verifier checks that every chosen id came back.
- **Sale price (DEV 15.11.2026, `core/sale.py`, `sale_step.py`):**
  - A sale exists only when the owner stated a sale price. It starts on the publish day, in the store timezone.
  - Its end is one explicit date in the owner's words ("30.11", "30.11.26", "30/11/2026"; no year means this year). Words, two dates, an impossible date or a past date mean the end is missing.
  - A missing end adds "תאריך סיום המבצע" to the questions. A sale price not below the price sends `sale_price_not_below`.
  - Either one keeps the job from generating (NEEDS_INFO). While waiting for approval, it saves the facts but makes no version, and "מאשר" asks again. So a version never shows a sale that would not be sent.
  - "בלי מבצע" removes every sale fact.
  - On create the store gets `product_special` (P1-02 shape), and the verifier checks price and dates. A sale whose end passed between approval and publish fails the job and is never sent.
- **Lifestyle image on request (DEV 15.11.2026):**
  - It is made only when `lifestyle_requested`. That comes from the first email (extract v4), a free reply (classify v4) or the exact command "תמונת אווירה".
  - It uses `image_lifestyle` with scene rules from the playbook (no people, nothing added), is attached as `image-3.jpg`, and is marked on the version.
  - Later versions take it back from the previous preview; it is never made again. "בלי תמונת אווירה" drops it.
  - A failure sends `lifestyle_unavailable`, and the preview says "לא נוצרה".
  - On create it goes as `images` (create only, P1-02), and the verifier checks it is on the store CDN.
  - Every uploaded image is deleted after the create.
- **One thread per product (DEV 22.11.2026):**
  - The first product of an owner email continues in the owner's own thread, with subject "Re: <his subject>". Every later product opens one thread of its own, with the fixed subject `_thread_subject` ("[job id] product name").
  - Every email of a job goes in its thread with that subject, and answers the newest message of the thread (`In-Reply-To`): the owner's latest reply, or the agent's last email.
  - So two products make two threads, also after a clarifying question. A reply in the owner's thread belongs to the first product.
- **The owner's own name (DEV 22.11.2026):**
  - A name in a reply to a preview becomes the exact title (`title_override`). Only surrounding quotes are removed, and line breaks become spaces. It is never rewritten or style-checked, so "!" and test markings are his choice.
  - The name in the first email is still written by `write_title`.
  - A later rewrite of the text keeps his name.
- **Image reuse changed (DEV 22.11.2026): nothing is stored.**
  - The earlier image comes back from the earlier job's own preview email (`image-2.jpg`, `image-3.jpg`).
  - Candidates are earlier jobs with the same store model, newest first, at most 5. They are matched by role, prompt, image model and photo hashes; the photos are re-read from each candidate's source email.
  - "תמונת אווירה" while one is attached makes a new one (`fresh`).
  - The bucket keeps only the publisher's short-lived uploads.
