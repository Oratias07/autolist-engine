---
module: core
status: approved
approved_by: DEV (Or)
approved_at: 2026-10-30
amendment_approved: 2026-10-30   # alert outbox, approved by DEV (Or) via PR #3
amendment_proposed: 2026-11-02   # P0-09 cap event, cost category, month summary - awaiting DEV approval
phase: 0 (parts marked P0), rest in later phases
---

# core

## Purpose
Shared foundation that every other module may import. It holds:
- the data models modules exchange
- the job state machine
- persistence of job state
- field provenance
- the cost ledger
- config and playbook loading
- logging with redaction

It contains no business decisions about products.

## Files
| File | Phase | Responsibility |
|---|---|---|
| `errors.py` | P0 | The error hierarchy listed under Errors. |
| `models.py` | P0 | Pydantic v2 models. P0 set: `StoreConfig`, `Provenance`, `SourcedValue[T]`, `CostEntry`, `Job` (skeleton), `JobState`. Product models (`ProductDraft`, `ProductVersion`) are added in the content phase, by updating this SPEC first. |
| `config.py` | P0 | Loads `$AUTOLIST_STORE_DIR/store.yaml` into `StoreConfig`. Any missing required key raises `ConfigMissing(key)`. There are no defaults for client values. |
| `logging.py` | P0 | Structured JSON logging plus a redaction filter (see Side effects). |
| `cost_ledger.py` | P0 | Records and enforces spend (see below). |
| `provenance.py` | P0 | `SourcedValue` helpers. `require_sources(obj)` raises `MissingProvenance([field paths])` for any non-null field without a source. |
| `state_machine.py` | P1 | Allowed transitions; `transition(job, to, reason)` raises on an illegal move. |
| `job_store.py` | P1 | Firestore persistence of `Job` with optimistic locking (version field). P0 uses an in-memory fake with the same interface. |
| `playbook_loader.py` | P2 | Loads and validates `playbook.yaml`. A rule with value `null` is returned as `Unknown`, never coerced. |

## Inputs
- `store.yaml`, read at startup.
- Calls from other modules.

## Outputs
- Model instances.
- `CostDecision(allowed: bool, reason)`.
- Exceptions listed below.

## Key models (P0)
```python
class Provenance(BaseModel):
    source: Literal["client_email", "supplier_invoice", "playbook", "research", "store_api", "system"]
    ref: str                 # message-id, invoice row, playbook key, URL, endpoint
    confidence: Literal["certain", "high", "medium", "low"]
    observed_at: datetime

class SourcedValue(BaseModel, Generic[T]):
    value: T | None          # None = unknown -> becomes a question; never replaced by a default
    provenance: list[Provenance]   # empty only when value is None

class CostEntry(BaseModel):
    at: datetime; job_id: str | None; provider: str; operation: str
    units: float; unit: str; usd: Decimal; estimated: bool
```

## Cost ledger (`cost_ledger.py`)
- **Before each call:** `authorize(job_id, provider, operation, est_usd) -> CostDecision`. Callers must call it before every paid request.
  - Refused if `job_spend + est > costs.per_job_cap`.
  - Refused if `month_spend + est > costs.monthly_cap`.
- **After each call:** `record(entry)`, with the real cost when the provider reports it, otherwise the estimate (`estimated=True`).
- **Alerts:** when month spend crosses a value in `costs.alert_thresholds`, it emits `CostThresholdCrossed(threshold)` exactly once per threshold per calendar month. The orchestrator passes that event to `channels`, which sends the owner email. `core` never sends anything.
- **Month boundaries:** calendar month in the timezone from `store.yaml`.
- **Storage:** Firestore in P1. A local JSON file in P0 spikes.

## Side effects
- **Logging only.** The redaction filter removes:
  - `Authorization` headers
  - any key matching `/token|key|secret|password|bearer/i`
  - any value that equals a loaded secret
- **Cost ledger storage** (P1+).
- **Never** network calls, email or store writes.

## Errors
`ConfigMissing`, `ConfigInvalid` (key present, wrong type or range), `MissingProvenance`, `IllegalTransition`, `CostCapExceeded`, `JobLocked`. All inherit from `AutolistError` and carry a machine-readable `code`.

## Allowed dependencies
Standard library, pydantic, pyyaml, google-cloud-firestore (`job_store` and `cost_ledger` only), google-cloud-secret-manager (`config.get_secret` only). No imports from any other autolist module.

## Not responsible for
- Sending alerts (`channels`).
- Deciding product content (`content`).
- Calling external APIs other than Firestore and Secret Manager.

## Acceptance criteria (P0)
1. Loading a `store.yaml` missing any required key raises `ConfigMissing` naming the key. The test covers every required key.
2. `require_sources` flags a nested field with a value and no provenance, and passes a `None` field.
3. Cost ledger:
   - with cap 60 and 59.50 spent, `authorize(est=0.67)` is refused
   - crossing 30, then 40, emits exactly two events, and repeating a call does not re-emit
   - a new month resets the state
4. Redaction: a log record containing `Authorization: Bearer abc`, `{"api_token": "x"}`, or the literal secret value shows none of them. This is checked by a test that captures log output.
5. Coverage is at least 90% for `core`, and `lint-imports` passes.

## Implementation notes (P0-03, 2026-10-30)
These record how the approved behavior was implemented. No behavior was added.
- **Config.**
  - Every key in `store.yaml` must be present. Unknown keys are rejected (`ConfigInvalid`), which catches typos.
  - A value documented as "filled later" may be `null`. `StoreConfig.require("a.b.c")` raises `ConfigMissing("a.b.c")` when code needs it.
  - The publishing switch can be `true` only with `enabled_by` and `enabled_at` set.
- **Cost ledger.**
  - The clock is injectable, so `authorize(job_id, provider, operation, est_usd)` keeps the SPEC signature.
  - `require(...)` is `authorize` that raises `CostCapExceeded`.
  - `record(entry)` returns the threshold events it newly triggered, and the orchestrator forwards them.
  - Storage: `InMemoryLedgerStore`, plus `JsonFileLedgerStore` for P0 spikes. Firestore comes in P1.
- **Job states (skeleton).**
  - Only the states named in the project doc: RECEIVED, NEEDS_INFO, RESEARCHING, GENERATING, AWAITING_APPROVAL, REVISING, APPROVED, PUBLISHING, PUBLISHED, FAILED, EXPIRED.
  - A state for `cancel` is not defined anywhere yet. It is left to the P1 state_machine SPEC (open question).

## Amendment (2026-10-30, approved): alert outbox
**Problem.** Paid calls are recorded deep inside modules (`llm`, later `images`), and those modules must not send anything. If `record()` only returns the threshold events, they are lost on the way, and the owner never gets the 30/40/50 USD alerts.

**Change.**
- The ledger also keeps every new threshold event in a persisted outbox (`pending_alerts`).
- `drain_alerts()` returns the outbox and clears it. The orchestrator calls it after each step and passes the events to `channels`.
- `record()` still returns the events, so nothing else changes.

**Acceptance.** Two crossings recorded through any module come out of `drain_alerts()` exactly once.

## Additions (2026-11-02, P0-08)
- `models.py`: channel models (`ChannelMessage`, `OutboundMessage` with `in_reply_to`, `Attachment`, `HealthStatus`). They moved here from `channels/base.py` per CLAUDE.md rule 3.
- `StoreConfig`: `secrets.gmail_oauth_client`, `channels.email.pubsub_topic`, `channels.email.pubsub_subscription`. These are required keys.

## Amendment (2026-11-02, **proposed**): cap event, cost category, month summary (P0-09)
**Problem.** P0-09 needs three things the ledger does not give today:
- an event when the monthly cap is reached
- spend split into text, images and search (the `cost_alert` template asks for it)
- one place that computes the numbers, so the orchestrator holds no cost arithmetic

**Change.**
1. **`CostCapReached(kind="cap", month, month_spend, monthly_cap)`.**
   - `record()` emits it once per month, the first time month spend is `>= monthly_cap`.
   - It goes into the same outbox.
   - `CostThresholdCrossed` gains `kind="threshold"`, and the outbox becomes a list of the union, discriminated by `kind`.
2. **`authorize()` refuses every call once month spend `>= monthly_cap`**, even a call with a zero estimate. Today it refuses only when `spend + estimate > cap`, so at exactly 60.00 a zero-estimate call would pass. This matches "at the monthly cap all paid calls stop".
3. **`CostEntry.category: Literal["llm", "image", "search"]`**, required, with no default.
   - The caller that knows what it paid for sets it. For example, `llm.client` sets `"llm"`.
   - This touches one core model plus the one place that builds it (`llm/client.py`).
   - The archived P0-07 `ledger.json` in the store repo stays as recorded. It is a report, and nothing loads it.
4. **`month_summary(at) -> CostSummary`** (read-only), with these fields:
   - `month`
   - `month_spend`
   - `by_category` (all three keys, 0 when none)
   - `jobs_count` (distinct non-null `job_id`)
   - `monthly_cap`
   - `next_threshold` (the next threshold above spend, else `monthly_cap`)
   - `reset_date` (first day of next month in `store.timezone`)
5. **`requeue_alerts(events)`** puts events back at the front of the outbox. The orchestrator calls it when a send fails.

**Acceptance.**
- Crossing the cap emits exactly one `CostCapReached` per month.
- `authorize()` at spend == cap refuses, even with estimate 0.
- `month_summary` on seeded entries returns the expected split, `next_threshold` (30.10 → 40, 50.10 → 60) and `reset_date` (2026-11-15 → 2026-12-01; 2026-12-31 23:30 Israel time → 2027-01-01).
- A `CostEntry` without `category` is rejected.
- Requeued events come out of the next drain first.
