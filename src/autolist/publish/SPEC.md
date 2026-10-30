---
module: publish
status: approved
approved_by: DEV (Or)
approved_at: 2026-10-30
phase: 0 (istores_client, rate_limiter); 8 (publisher, verifier); 2 (catalog_sync)
---

# publish

## Purpose
The only module that talks to the store. It writes nothing unless called by the publisher with an approved version, and the client refuses by construction anything outside its allowlist.

## Files
| File | Phase | Responsibility |
|---|---|---|
| `istores_client.py` | P0 | Typed iStores REST client with allowlist, own-ID guard, redaction. |
| `rate_limiter.py` | P0 | Token-bucket limiter per token: max 2 req/s, honors `Retry-After` on 429. |
| `catalog_sync.py` | P2 | Read-only catalog scan for the Playbook (products, categories, misc). |
| `verifier.py` | P8 | GET after create; compares every field to the approved version. |
| `publisher.py` | P8 | Hidden create → verify → unhide, with locking and the duplicate check. |

## istores_client — interface (P0)
Base URL and Company ID from `store.yaml`. Headers on every request:
- `Authorization: Bearer <token from Secret Manager>`
- `Company-Id`
- `User-Agent: autolist/<version>`
- `Content-Type: application/json`

**Allowed calls.** Any other method or path raises `EndpointNotAllowed` before any I/O:

| Method | Path | Notes |
|---|---|---|
| GET | `/products/{limit}/{page}` | list |
| GET | `/products/{id}` | read |
| POST | `/products` | create. Body must contain `hidden: 1`, else `UnsafeCreate`. Response is text like `productID: 970`: parse with regex. If parsing fails, raise `CreateResultUnknown` (the caller then searches by SKU). Never auto-retried. |
| PUT | `/products/{id}` | only if `id` is in the `created_products` registry, else `ForeignProductError` |
| DELETE | `/products/{id}` | same guard. Used only by P0 cleanup and tests. |
| GET | `/categories…`, `/misc/…` | read-only lookups (exact paths are confirmed in P0-03 and written into this table) |

`PUT /products/bulk` is **not** allowed: one write per product keeps the guard simple.

**Registry.** `created_products` (Firestore in P1, a local JSON file in P0) stores `{product_id, created_at, job_id}`. The ID is written immediately after a successful POST parse and before any other call.

**Unhide rule** (enforced in the client, not only in the publisher). A PUT that sets `hidden: 0` or `status: 1` is refused unless **both** gates pass:
1. **Store switch.** `store.yaml` → `publishing.unhide_enabled` is `true`, and `enabled_by` and `enabled_at` are set.
   - The switch is turned on only on an explicit instruction from the owner or the developer that the system is ready. The instruction is recorded as a decisions row, and the switch is changed by PR.
   - While it is off, approved products are created and verified but stay hidden ("ready" state). The refusal raises `UnhideDisabled`.
   - The agent cannot change the switch: no tool writes `store.yaml`.
2. **Product check.** The current product, read by GET in the same call, has `price > 0`, at least one category, and a non-empty name in every language required by `store.yaml`. Otherwise the refusal raises `UnsafeUnhide`.

## rate_limiter (P0)
- At most 2 requests/second per token. Concurrency-safe (asyncio lock).
- On 429:
  - sleep `Retry-After` (seconds or HTTP date) and retry, at most 3 times
  - with no header, back off exponentially starting at 1s
- Non-idempotent requests (POST) are **never** retried on timeout or 5xx. Only GET, and PUT with the same body, are.

## Side effects
HTTPS calls to iStores. Registry writes. Structured logs, with the token redacted and bodies truncated to 2 KB.

## Errors
`EndpointNotAllowed`, `ForeignProductError`, `UnsafeCreate`, `UnsafeUnhide`, `UnhideDisabled`, `CreateResultUnknown`, `StoreRateLimited` (retries exhausted), `StoreAPIError(status, body_excerpt)`.

## Allowed dependencies
`autolist.core`, `httpx`. No imports from other modules.

## Not responsible for
- Deciding what to publish.
- Building the product body (`content` builds it; `publisher` passes it through).
- Email.

## Acceptance criteria (P0)
1. Unit tests: every disallowed path, including `/orders`, `/order/1` and `/customers`, raises `EndpointNotAllowed`, and the transport mock records zero calls.
2. PUT or DELETE on an ID not in the registry raises `ForeignProductError` with zero calls.
3. POST without `hidden: 1` raises `UnsafeCreate`.
4. An unhide PUT raises `UnhideDisabled` while `publishing.unhide_enabled` is false, even for a valid product. With the switch on, an unhide PUT on a product with price 0 raises `UnsafeUnhide`. Both cases make zero write calls.
5. Rate limiter:
   - 10 queued GETs take at least 4.5s
   - a mocked 429 with `Retry-After: 2` waits at least 2s and then succeeds
   - a POST that times out is not retried
6. Live (P0-03/P0-04, against the real store, read-only plus one hidden test product). Results are recorded in `$AUTOLIST_STORE_DIR/docs/open-questions.md`:
   - list, read, categories, languages and stock statuses return 200
   - a hidden test product is created, read back, then deleted, and is never visible on the storefront
   - whether image URLs are copied to the CDN is checked on that product
