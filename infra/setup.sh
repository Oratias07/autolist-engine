#!/usr/bin/env bash
# autolist infrastructure setup, part 1 (infra/SPEC.md, P1-03).
#
# Run by the developer in Cloud Shell, logged in to the client's Google account.
# Idempotent: every step creates only what is missing and changes nothing that exists.
# Part 2 (push subscription, scheduler jobs) runs after the first deploy, because it needs
# the service URL.
#
# Required environment (client values come from the store repo's store.yaml):
#   PROJECT   GCP project id
#   REGION    Cloud Run / Tasks / Artifact Registry region
#   FS_LOCATION  Firestore location (cannot be changed later)
#   BUCKET    private reference bucket (must already exist)
#   SECRETS   space-separated Secret Manager names the service reads
#   CI_SA     email of the CI service account used by the deploy workflow
set -euo pipefail

: "${PROJECT:?}" "${REGION:?}" "${FS_LOCATION:?}" "${BUCKET:?}" "${SECRETS:?}" "${CI_SA:?}"

RUN_SA="autolist-run@${PROJECT}.iam.gserviceaccount.com"
INVOKE_SA="autolist-invoke@${PROJECT}.iam.gserviceaccount.com"
REPO="autolist"
QUEUE="autolist-jobs"
G="gcloud --project=${PROJECT} --quiet"

say() { printf '\n== %s\n' "$1"; }
have() { "$@" >/dev/null 2>&1; }

say "1/7 APIs"
$G services enable run.googleapis.com cloudtasks.googleapis.com cloudscheduler.googleapis.com \
  firestore.googleapis.com artifactregistry.googleapis.com iamcredentials.googleapis.com

say "2/7 service accounts"
for pair in "autolist-run:autolist runtime" "autolist-invoke:autolist invoker (push, tasks, scheduler)"; do
  name="${pair%%:*}"; desc="${pair#*:}"
  if have $G iam service-accounts describe "${name}@${PROJECT}.iam.gserviceaccount.com"; then
    echo "exists: ${name}"
  else
    $G iam service-accounts create "${name}" --display-name="${desc}"
    echo "created: ${name}"
  fi
done
# a new service account can take a few seconds before it accepts role bindings
for sa in "${RUN_SA}" "${INVOKE_SA}"; do
  for _ in 1 2 3 4 5 6; do have $G iam service-accounts describe "${sa}" && break; sleep 5; done
done

say "3/7 Firestore (native mode, location ${FS_LOCATION}, cannot be changed later)"
if have $G firestore databases describe --database="(default)"; then
  echo "exists: (default) at $($G firestore databases describe --database='(default)' --format='value(locationId)')"
else
  $G firestore databases create --database="(default)" --location="${FS_LOCATION}" --type=firestore-native
  echo "created: (default)"
fi

say "4/7 Artifact Registry repository ${REPO}"
if have $G artifacts repositories describe "${REPO}" --location="${REGION}"; then
  echo "exists: ${REPO}"
else
  $G artifacts repositories create "${REPO}" --repository-format=docker --location="${REGION}" \
    --description="autolist service images"
  echo "created: ${REPO}"
fi
POLICY_FILE="$(mktemp)"
cat > "${POLICY_FILE}" <<'JSON'
[
  {"name": "keep-3", "action": {"type": "Keep"}, "mostRecentVersions": {"keepCount": 3}},
  {"name": "delete-older", "action": {"type": "Delete"}, "condition": {"olderThan": "1d"}}
]
JSON
if $G artifacts repositories set-cleanup-policies "${REPO}" --location="${REGION}" \
  --policy="${POLICY_FILE}" --no-dry-run >/dev/null 2>&1; then
  echo "cleanup policy: keep the 3 newest images"
else
  echo "WARNING: cleanup policy was not set (not blocking; report this line to the developer)"
fi
rm -f "${POLICY_FILE}"

say "5/7 Cloud Tasks queue ${QUEUE} (one job at a time, no retries)"
if have $G tasks queues describe "${QUEUE}" --location="${REGION}"; then
  echo "exists: ${QUEUE}"
else
  $G tasks queues create "${QUEUE}" --location="${REGION}" --max-attempts=1 \
    --max-concurrent-dispatches=1
  echo "created: ${QUEUE}"
fi

say "6/7 runtime permissions (${RUN_SA})"
for secret in ${SECRETS}; do
  $G secrets add-iam-policy-binding "${secret}" --member="serviceAccount:${RUN_SA}" \
    --role=roles/secretmanager.secretAccessor >/dev/null
  $G secrets add-iam-policy-binding "${secret}" --member="serviceAccount:${RUN_SA}" \
    --role=roles/secretmanager.viewer >/dev/null
  echo "secret access: ${secret}"
done
$G projects add-iam-policy-binding "${PROJECT}" --member="serviceAccount:${RUN_SA}" \
  --role=roles/datastore.user --condition=None >/dev/null
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" --project="${PROJECT}" \
  --member="serviceAccount:${RUN_SA}" --role=roles/storage.objectAdmin >/dev/null
$G iam service-accounts add-iam-policy-binding "${RUN_SA}" --member="serviceAccount:${RUN_SA}" \
  --role=roles/iam.serviceAccountTokenCreator >/dev/null
$G tasks queues add-iam-policy-binding "${QUEUE}" --location="${REGION}" \
  --member="serviceAccount:${RUN_SA}" --role=roles/cloudtasks.enqueuer >/dev/null
$G iam service-accounts add-iam-policy-binding "${INVOKE_SA}" --member="serviceAccount:${RUN_SA}" \
  --role=roles/iam.serviceAccountUser >/dev/null
echo "firestore, bucket, url signing, queue: granted"

say "7/7 deploy permissions (${CI_SA})"
$G artifacts repositories add-iam-policy-binding "${REPO}" --location="${REGION}" \
  --member="serviceAccount:${CI_SA}" --role=roles/artifactregistry.writer >/dev/null
# project level: the service does not exist before the first deploy, so it cannot hold the binding
$G projects add-iam-policy-binding "${PROJECT}" --member="serviceAccount:${CI_SA}" \
  --role=roles/run.developer --condition=None >/dev/null
$G iam service-accounts add-iam-policy-binding "${RUN_SA}" --member="serviceAccount:${CI_SA}" \
  --role=roles/iam.serviceAccountUser >/dev/null
echo "image push, service deploy: granted"

say "summary"
$G iam service-accounts list --format="value(email)" --filter="email~^autolist-"
echo "firestore: $($G firestore databases describe --database='(default)' --format='value(locationId,type)')"
echo "registry:  $($G artifacts repositories describe "${REPO}" --location="${REGION}" --format='value(name)')"
echo "queue:     $($G tasks queues describe "${QUEUE}" --location="${REGION}" --format='value(name,retryConfig.maxAttempts,rateLimits.maxConcurrentDispatches)')"
echo "DONE part 1"
