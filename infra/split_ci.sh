#!/usr/bin/env bash
# Split the CI identity in two (security review 22.11.2026, H1). Run by the developer in
# Cloud Shell, one step at a time, in order. Every step only adds or removes IAM bindings;
# no secret value is read or printed.
#
#   live runs  -> autolist-ci      (the secrets the live runs need; nothing to deploy with)
#   deploys    -> autolist-deploy  (deploy this one service; no secret values)
#
# Each identity can be used only by its own workflow file on the store repo's main branch.
#
# Usage:
#   export PROJECT=<gcp-project> REGION=<region> STORE_REPO=<owner>/<repo> PROVIDER=<wif-provider>
#   export SERVICE=autolist-dev SECRETS="<secret names from store.yaml, space separated>"
#   bash infra/split_ci.sh 1   # WIF: also map the workflow file of the run
#   bash infra/split_ci.sh 2   # create autolist-deploy and give it the deploy rights
#   (then: the store repo's deploy.yml uses autolist-deploy; one deploy must pass)
#   bash infra/split_ci.sh 3   # take the deploy rights away from autolist-ci; bind it to live.yml
#   bash infra/split_ci.sh show
set -euo pipefail
: "${PROJECT:?}" "${REGION:?}" "${STORE_REPO:?}" "${PROVIDER:?}" "${SERVICE:?}" "${SECRETS:?}"
POOL="${POOL:-github}"
REPO="${REPO:-autolist}"
G="gcloud --project=${PROJECT} --quiet"
NUM="$($G projects describe "${PROJECT}" --format='value(projectNumber)')"
POOL_ID="projects/${NUM}/locations/global/workloadIdentityPools/${POOL}"
CI_SA="autolist-ci@${PROJECT}.iam.gserviceaccount.com"
DEPLOY_SA="autolist-deploy@${PROJECT}.iam.gserviceaccount.com"
RUN_SA="autolist-run@${PROJECT}.iam.gserviceaccount.com"
WF="${POOL_ID}/attribute.workflow_ref/${STORE_REPO}/.github/workflows"
LIVE_MEMBER="principalSet://iam.googleapis.com/${WF}/live.yml@refs/heads/main"
DEPLOY_MEMBER="principalSet://iam.googleapis.com/${WF}/deploy.yml@refs/heads/main"

wif_members() {  # workloadIdentityUser members of a service account, one per line
  $G iam service-accounts get-iam-policy "$1" --format=json | python3 -c '
import json, sys
p = json.load(sys.stdin)
for b in p.get("bindings", []):
    if b["role"] == "roles/iam.workloadIdentityUser":
        print("\n".join(b["members"]))'
}

case "${1:-}" in
1)
  # keep every existing mapping and the repo/branch condition; add attribute.workflow_ref
  mapping="$($G iam workload-identity-pools providers describe "${PROVIDER}" --location=global \
    --workload-identity-pool="${POOL}" --format=json | python3 -c '
import json, sys
m = json.load(sys.stdin)["attributeMapping"]
m["attribute.workflow_ref"] = "assertion.workflow_ref"
print(",".join(f"{k}={v}" for k, v in m.items()))')"
  $G iam workload-identity-pools providers update-oidc "${PROVIDER}" --location=global \
    --workload-identity-pool="${POOL}" --attribute-mapping="${mapping}" >/dev/null
  echo "step 1 done: attribute.workflow_ref is mapped"
  ;;
2)
  if ! $G iam service-accounts describe "${DEPLOY_SA}" >/dev/null 2>&1; then
    $G iam service-accounts create autolist-deploy --display-name="autolist deploy (no secrets)"
    for _ in 1 2 3 4 5 6; do $G iam service-accounts describe "${DEPLOY_SA}" >/dev/null 2>&1 && break; sleep 5; done
  fi
  $G artifacts repositories add-iam-policy-binding "${REPO}" --location="${REGION}" \
    --member="serviceAccount:${DEPLOY_SA}" --role=roles/artifactregistry.writer >/dev/null
  for role in roles/run.developer roles/run.invoker; do  # invoker: the /health check only (app gate)
    $G run services add-iam-policy-binding "${SERVICE}" --region="${REGION}" \
      --member="serviceAccount:${DEPLOY_SA}" --role="${role}" >/dev/null
  done
  $G iam service-accounts add-iam-policy-binding "${RUN_SA}" \
    --member="serviceAccount:${DEPLOY_SA}" --role=roles/iam.serviceAccountUser >/dev/null
  for s in ${SECRETS}; do  # the stage gate reads labels; viewer never reads a value
    $G secrets add-iam-policy-binding "$s" --member="serviceAccount:${DEPLOY_SA}" \
      --role=roles/secretmanager.viewer >/dev/null
  done
  $G iam service-accounts add-iam-policy-binding "${DEPLOY_SA}" --member="${DEPLOY_MEMBER}" \
    --role=roles/iam.workloadIdentityUser >/dev/null
  echo "step 2 done: ${DEPLOY_SA} can deploy ${SERVICE}, only from deploy.yml on main"
  ;;
3)
  $G iam service-accounts add-iam-policy-binding "${CI_SA}" --member="${LIVE_MEMBER}" \
    --role=roles/iam.workloadIdentityUser >/dev/null
  wif_members "${CI_SA}" | while read -r m; do
    [ "$m" = "${LIVE_MEMBER}" ] && continue
    $G iam service-accounts remove-iam-policy-binding "${CI_SA}" --member="$m" \
      --role=roles/iam.workloadIdentityUser >/dev/null
    echo "removed from ${CI_SA}: $m"
  done
  $G projects remove-iam-policy-binding "${PROJECT}" --member="serviceAccount:${CI_SA}" \
    --role=roles/run.developer --condition=None >/dev/null || true
  $G artifacts repositories remove-iam-policy-binding "${REPO}" --location="${REGION}" \
    --member="serviceAccount:${CI_SA}" --role=roles/artifactregistry.writer >/dev/null || true
  $G iam service-accounts remove-iam-policy-binding "${RUN_SA}" \
    --member="serviceAccount:${CI_SA}" --role=roles/iam.serviceAccountUser >/dev/null || true
  $G run services remove-iam-policy-binding "${SERVICE}" --region="${REGION}" \
    --member="serviceAccount:${CI_SA}" --role=roles/run.invoker >/dev/null || true
  echo "step 3 done: ${CI_SA} is for live.yml only, and cannot deploy"
  ;;
show)
  for sa in "${CI_SA}" "${DEPLOY_SA}"; do echo "== ${sa} can be used by:"; wif_members "$sa" || true; done
  echo "== project roles of the two accounts:"
  $G projects get-iam-policy "${PROJECT}" --format=json | python3 -c '
import json, sys
for b in json.load(sys.stdin)["bindings"]:
    for m in b["members"]:
        if "autolist-ci@" in m or "autolist-deploy@" in m:
            print(b["role"], m)'
  echo "== service ${SERVICE}:"
  $G run services get-iam-policy "${SERVICE}" --region="${REGION}" --format='table(bindings.role,bindings.members)'
  ;;
*)
  echo "usage: $0 1|2|3|show" >&2
  exit 2
  ;;
esac
