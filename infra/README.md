# infra

Terraform for GCP arrives in phase 1. It is generic: client-specific values (project, secret names, repo) come from the store repo.

Phase-0 resources were created by hand. Their inventory and the go-live checklist live in the client's store repo (`docs/infra.md`), and phase 1 **imports** them instead of creating duplicates.

Fixed rules the Terraform must keep:
- The production deploy refuses any secret version labelled `stage=dev`.
- CI and live runs authenticate with Workload Identity Federation, limited to one repo and `refs/heads/main`. No service-account keys.
- Service accounts get per-secret `secretAccessor`, never project-wide roles.
