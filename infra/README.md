# infra

Setup is specified in `infra/SPEC.md`: an idempotent Cloud Shell script for the fast track (DEV decision, 14.11.2026). Terraform moves to phase 9 and imports what the script created. Everything is generic: client-specific values (project, secret names, repo) come from the store repo.

Phase-0 resources were created by hand. Their inventory and the go-live checklist live in the client's store repo (`docs/infra.md`). The script creates only what is missing.

Fixed rules every setup method must keep:
- The production deploy refuses any secret version labelled `stage=dev`.
- CI and live runs authenticate with Workload Identity Federation, limited to one repo and `refs/heads/main`. No service-account keys.
- Service accounts get per-secret `secretAccessor`, never project-wide roles.
