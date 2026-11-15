---
module: infra
status: approved         # P1-03, fast track: the smallest setup that lets an owner email start a job
approved_by: DEV (Or)
approved_at: 2026-11-14  # PR #34 + chat: script setup, us-central1 / nam5, dev stage, limits below
phase: 1
---

# infra

## Purpose
Run the engine as a service that wakes up when the agent mailbox gets mail, works on the job, and goes back to zero. Nothing runs between emails.

## What exists after setup
| Resource | Name (from `store.yaml`) | Notes |
| --- | --- | --- |
| Cloud Run service | `autolist-<stage>` | No public access. Scales to zero, max 1 instance, request timeout just above the job step limit. |
| Runtime service account | `autolist-run` | Roles below. No project-wide role. |
| Invoker service account | `autolist-invoke` | Only `run.invoker` on the service. Used by Pub/Sub push, Cloud Tasks and Scheduler (OIDC). |
| Pub/Sub push subscription | `gmail-agent-run` on the existing topic | Pushes to `/events/gmail`. The phase-0 pull subscription stays for spike checks. |
| Cloud Tasks queue | `autolist-jobs` | One job at a time, no automatic retry of a failed job step. |
| Cloud Scheduler job | `renew-gmail-watch`, daily | Calls `/cron/renew-watch`. Closes store open question #26. |
| Firestore (native mode, default database) | – | Collections below. |
| Artifact Registry repository | `autolist` | Holds the container image. Old images are cleaned up, keep 3. |

**Runtime service account roles:**
- `secretAccessor` on each secret named in `store.yaml`, one binding per secret
- `datastore.user` on the project (Firestore has no per-collection IAM)
- `storage.objectAdmin` on the reference bucket only
- `serviceAccountTokenCreator` on itself (signed URLs)
- `cloudtasks.enqueuer` on the queue, and `serviceAccountUser` on `autolist-invoke`

## Firestore collections
| Collection | Holds | Replaces |
| --- | --- | --- |
| `jobs` | one document per job: state, thread id, versions | – (new) |
| `created_products` | the registry of products this system created | the JSON file registry |
| `cost_ledger` | one document per month: entries, total, alerts sent, cap flag; plus `_outbox` for undelivered alerts. Job totals are rebuilt from entries on load | the JSON ledger files |
| `channel_state` | Gmail `historyId`, watch expiry | – |
| `processed_messages` | Gmail message ids already handled | – |

`processed_messages` exists because Pub/Sub delivers at least once. A message id seen before is acknowledged and skipped.

## Entrypoints (HTTP, all require an OIDC token from `autolist-invoke`)
| Path | Called by | Does |
| --- | --- | --- |
| `POST /events/gmail` | Pub/Sub push | Reads new message ids, verifies the sender, records the message, enqueues a task. Returns within seconds. |
| `POST /tasks/job` | Cloud Tasks | Runs one job step (minutes). |
| `POST /cron/renew-watch` | Scheduler | Renews the Gmail watch, stores the new expiry. |
| `POST /cron/watchdog` | Scheduler | Fails stuck jobs and tells the owner and the developer contact. |
| `GET /health` | deploy check | Config loads, secrets are readable (metadata only), Firestore reachable. |

There is no public endpoint in this phase. The owner's preview goes by email (P1-07).

## Stages
- `store.yaml` gets `deploy.stage: dev | prod`.
- The deploy script reads the labels of every secret version the service will use.
  - `--stage prod` refuses if any has `stage=dev`. This is the existing rule, unchanged.
  - `--stage dev` deploys with dev secrets. The test bot is a dev-stage service.
- A dev-stage service keeps every store safety rule: hidden and disabled creates, the unhide switch, the cost caps, the sender allowlist.

## Setup and deploy
**Decided by DEV (14.11.2026); this replaces the earlier Terraform-in-phase-1 plan.**
- **Fast track:** an idempotent script, `infra/setup.sh`, that DEV runs in Cloud Shell. Every command is `create if missing`. It prints what it created and changes nothing that exists.
- **Why:** Terraform needs a state bucket, an import of 12 hand-made resources and a deployer identity with broad project roles. That is more setup than the service itself. The owner's account runs Cloud Shell anyway.
- **What is kept for later:** Terraform moves to phase 9 (hardening), where the script's resource list becomes the import list.

**Deploy:** a `deploy` workflow in the store repo (manual, `main` only, WIF). It builds the image from the engine at a pinned commit, pushes it, and updates the Cloud Run service.
- This needs three more roles on `autolist-ci`: `artifactregistry.writer` on the repository, `run.developer` on the project, `serviceAccountUser` on `autolist-run`.
  - `run.developer` is project-level because the service does not exist before the first deploy and cannot hold the binding yet.
- `infra/setup.sh` is part 1 (everything that does not need the service URL). Part 2 runs after the first deploy: the push subscription, the two Scheduler jobs and `run.invoker` for `autolist-invoke`.
- The store repo content (playbook, prompts, replies, `store.yaml`) is copied into the image at build time, so a deploy is also how a prompt or playbook change goes live.

## Region
Decided by DEV (14.11.2026): `us-central1`, the same as the bucket, and Firestore location `nam5` (US multi-region). The Firestore location cannot be changed later.

## Limits against runaway work (added after DEV review, 14.11.2026)
- **Active work limit per job step:** `agent.job_step_timeout_s` in `store.yaml` (DEV: 10 minutes).
  - A step that passes it is marked failed, and the owner gets the failure reply.
  - Waiting for the owner's answer is not active work and has no server cost.
  - The Cloud Run request timeout is set just above this value, not at 30 minutes.
- **No retry loops:**
  - Cloud Tasks: `max_attempts: 1`.
  - The Pub/Sub push subscription has a 10-minute message retention and no dead-letter redelivery. `/events/gmail` always acknowledges once the message id is recorded.
- **Max instances: 1.** The service cannot scale out.
- **Receipt and failure are always told, never implied by silence:**
  - Every accepted email gets a receipt reply at once. It says a preview follows within the time limit, and whom to contact if it does not (`agent.developer_contact` in `store.yaml`).
  - Every failed step sends the failure reply.
- **Watchdog:** a second daily Scheduler call, `/cron/watchdog`, finds jobs in an active state longer than the limit, marks them failed, replies to the owner and alerts the developer contact.

## Cost
Designed to stay inside the Always Free quotas at the expected volume (tens of products a month). The real number comes from Google's billing and goes into the weekly cost report (store backlog B-01).

## New dependencies (engine)
`starlette` + `uvicorn` (HTTP entrypoints), `google-cloud-tasks`. `google-cloud-firestore` and `google-cloud-storage` are already in.

## Not responsible for
- Job logic (orchestrator) and module behaviour.
- Rotating keys (go-live checklist in the store repo).

## Acceptance criteria (P1-03)
1. `infra/setup.sh` run twice in a row: the second run creates nothing and exits 0.
2. The service URL returns 403 without a token.
3. After deploy, `/health` (called with the invoker's token by the deploy workflow) returns ok.
4. A deploy with `--stage prod` while any secret has `stage=dev` is refused, and nothing is deployed.
5. An email from the allowlisted sender produces one `processed_messages` document and one queued task. The same Pub/Sub message delivered twice produces no second task.
6. `renew-watch` run by Scheduler moves the stored watch expiry forward.
8. A job step forced past the limit ends as failed, the owner gets the failure reply, and no task is retried.
9. The watchdog fails a job left in an active state and sends both messages.
7. Firestore-backed registry and ledger pass the same unit tests as the in-memory ones.


## Service image, deploy and part 2 (P1-03, 15.11.2026)
- **Image:** `Dockerfile` at the engine root. Python 3.12 slim, `uv sync --frozen --no-dev`, runs as a non-root user, `uvicorn autolist.orchestrator.main:app`. The build context has `engine/` and `store/`. `store/` holds only `store.yaml`, `playbook/`, `agent/` and `docs/voice.md`: no fixtures, no live runs. Engine CI builds the image with an empty store and checks that the app module imports.
- **`orchestrator/main.py`:** wiring only. Clients and secrets are built on first use, so a missing secret fails the request that needs it and shows in `/health`. The app does not crash at start. The only environment value is `AUTOLIST_SERVICE_URL`, set by the deploy workflow after the first deploy. `gcp.tasks_queue` and `gcp.invoker_service_account` are new `store.yaml` keys.
- **Blocking work off the event loop:** a job step runs in its own thread and event loop. The Gmail push, health and watchdog handlers run in the thread pool. A long step never delays a push acknowledgement.
- **Deploy (store repo `.github/workflows/deploy.yml`):**
  - Runs manually, from `main` only, with a full engine commit sha.
  - Stage gate: `prod` refuses while any secret is labelled `stage=dev`.
  - Builds and pushes the image, then `gcloud run deploy autolist-<stage>`. The service is not public, runs 0–1 instances with concurrency 8, 1 CPU and 1 GiB. The request timeout is `agent.job_step_timeout_s` + 60 s.
  - Checks that a call without a token gets 403. Once part 2 has granted the CI account `run.invoker`, it also checks `/health` with an ID token.
- **Part 2 (`infra/setup-part2.sh`, DEV in Cloud Shell after the first deploy):**
  - `run.invoker` for `autolist-invoke` and `autolist-ci`.
  - The Pub/Sub service agent may sign the push with the invoker's token.
  - Push subscription `<service>-push`: OIDC, 10-minute retention, no dead letter.
  - Two daily Scheduler jobs, with no retries: `renew-watch` at 06:17 and `watchdog` at 06:43, in the store's time zone.
  - One immediate `renew-watch` run, so the mailbox is watched from that moment.

- **Observed 15.11.2026 (first deploy, run 43708500564):** `/healthz` got Google's own 404 before reaching the service. Cloud Run reserves some paths that end in `z` (Cloud Run known issues: avoid every path ending in `z`). The health route is now `/health`. No other route ends in `z`.
