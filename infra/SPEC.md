---
module: infra
status: proposed         # P1-03, fast track: the smallest setup that lets an owner email start a job
proposed_at: 2026-11-14
phase: 1
---

# infra

## Purpose
Run the engine as a service that wakes up when the agent mailbox gets mail, works on the job, and goes back to zero. Nothing runs between emails.

## What exists after setup
| Resource | Name (from `store.yaml`) | Notes |
| --- | --- | --- |
| Cloud Run service | `autolist-<stage>` | No public access. Scales to zero, max 1 instance, request timeout 30 min. |
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
| `cost_ledger` | one document per month: entries, totals, alerts sent | the JSON ledger files |
| `channel_state` | Gmail `historyId`, watch expiry | – |
| `processed_messages` | Gmail message ids already handled | – |

`processed_messages` exists because Pub/Sub delivers at least once. A message id seen before is acknowledged and skipped.

## Entrypoints (HTTP, all require an OIDC token from `autolist-invoke`)
| Path | Called by | Does |
| --- | --- | --- |
| `POST /events/gmail` | Pub/Sub push | Reads new message ids, verifies the sender, records the message, enqueues a task. Returns within seconds. |
| `POST /tasks/job` | Cloud Tasks | Runs one job step (minutes). |
| `POST /cron/renew-watch` | Scheduler | Renews the Gmail watch, stores the new expiry. |
| `GET /healthz` | deploy check | Config loads, secrets are readable (metadata only), Firestore reachable. |

There is no public endpoint in this phase. The owner's preview goes by email (P1-07).

## Stages
- `store.yaml` gets `deploy.stage: dev | prod`.
- The deploy script reads the labels of every secret version the service will use.
  - `--stage prod` refuses if any has `stage=dev`. This is the existing rule, unchanged.
  - `--stage dev` deploys with dev secrets. The test bot is a dev-stage service.
- A dev-stage service keeps every store safety rule: hidden and disabled creates, the unhide switch, the cost caps, the sender allowlist.

## Setup and deploy
**Decision needed (contradicts the current plan, so it is proposed, not assumed).** `infra/README.md` says phase 1 uses Terraform and imports the phase-0 resources.
- **Proposal for the fast track:** an idempotent script, `infra/setup.sh`, that DEV runs in Cloud Shell. Every command is `create if missing`. It prints what it created and changes nothing that exists.
- **Why:** Terraform needs a state bucket, an import of 12 hand-made resources and a deployer identity with broad project roles. That is more setup than the service itself. The owner's account runs Cloud Shell anyway.
- **What is kept for later:** Terraform moves to phase 9 (hardening), where the script's resource list becomes the import list.

**Deploy:** a `deploy` workflow in the store repo (manual, `main` only, WIF). It builds the image from the engine at a pinned commit, pushes it, and updates the Cloud Run service.
- This needs three more roles on `autolist-ci`: `artifactregistry.writer` on the repository, `run.developer` on the service, `serviceAccountUser` on `autolist-run`.
- The store repo content (playbook, prompts, replies, `store.yaml`) is copied into the image at build time, so a deploy is also how a prompt or playbook change goes live.

## Region
`gcp.region` is null today. Proposal: `us-central1`, the same as the bucket. Firestore location `nam5` (US multi-region, the free-tier default) cannot be changed later.

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
3. After deploy, `/healthz` (called with the invoker's token by the deploy workflow) returns ok.
4. A deploy with `--stage prod` while any secret has `stage=dev` is refused, and nothing is deployed.
5. An email from the allowlisted sender produces one `processed_messages` document and one queued task. The same Pub/Sub message delivered twice produces no second task.
6. `renew-watch` run by Scheduler moves the stored watch expiry forward.
7. Firestore-backed registry and ledger pass the same unit tests as the in-memory ones.
