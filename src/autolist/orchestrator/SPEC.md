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
