# infra

Terraform for GCP arrives in phase 1. Phase 0 resources are created by hand and listed here, so phase 1 can import them instead of creating duplicates.

| Resource | Name | Created by | Phase |
|---|---|---|---|
| Project | (from store.yaml `gcp.project_id`) | DEV | done |
| Secret | istores token (name in store.yaml) | DEV | done, `stage=dev` |
| Secret | anthropic api key | DEV | P0-07 |
| Secret | gemini api key | DEV | P0-10 |
| Secret | gmail agent refresh token | DEV | P0-08 |
| Pub/Sub topic | gmail push | DEV | P0-08 |
| GCS bucket | private, reference images, signed URLs only | DEV | P0-06 |

The production deploy refuses to run if any referenced secret version has label `stage=dev`. Every key is rotated before go-live.
