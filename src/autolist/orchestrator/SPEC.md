---
module: orchestrator
status: proposed         # P0-09 skeleton only (cost-alert delivery); the rest is written at the start of phase 1
proposed_at: 2026-11-02
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
- `channel`: anything implementing `channels.base.Channel`.
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
