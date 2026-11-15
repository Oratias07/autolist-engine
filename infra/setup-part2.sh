#!/usr/bin/env bash
# autolist infrastructure setup, part 2 (infra/SPEC.md, P1-03). Runs after the first deploy.
#
# Run by the developer in Cloud Shell, logged in to the client's Google account.
# Idempotent: every step creates only what is missing and changes nothing that exists.
#
# Required environment (client values come from the store repo's store.yaml):
#   PROJECT   GCP project id
#   REGION    region of the Cloud Run service and Scheduler jobs
#   SERVICE   Cloud Run service name (autolist-<stage>)
#   TOPIC     Pub/Sub topic that Gmail watch publishes to (channels.email.pubsub_topic)
#   CI_SA     email of the CI service account (deploy workflow calls /healthz)
#   TIME_ZONE time zone of the scheduler jobs (store.timezone)
set -euo pipefail

: "${PROJECT:?}" "${REGION:?}" "${SERVICE:?}" "${TOPIC:?}" "${CI_SA:?}" "${TIME_ZONE:?}"

INVOKE_SA="autolist-invoke@${PROJECT}.iam.gserviceaccount.com"
SUB="${SERVICE}-push"
G="gcloud --project=${PROJECT} --quiet"

say() { printf '\n== %s\n' "$1"; }
have() { "$@" >/dev/null 2>&1; }

say "1/5 service URL"
URL="$($G run services describe "${SERVICE}" --region="${REGION}" --format='value(status.url)')"
[ -n "${URL}" ] || { echo "service ${SERVICE} not found: deploy first" >&2; exit 1; }
echo "${URL}"

say "2/5 who may call the service (nobody else: the service is not public)"
$G run services add-iam-policy-binding "${SERVICE}" --region="${REGION}" \
  --member="serviceAccount:${INVOKE_SA}" --role=roles/run.invoker >/dev/null
$G run services add-iam-policy-binding "${SERVICE}" --region="${REGION}" \
  --member="serviceAccount:${CI_SA}" --role=roles/run.invoker >/dev/null
echo "run.invoker: ${INVOKE_SA}, ${CI_SA}"

say "3/5 Gmail push subscription (10-minute retention, no dead letter, no retry loop)"
NUMBER="$($G projects describe "${PROJECT}" --format='value(projectNumber)')"
# Pub/Sub signs the push request with the invoker's OIDC token
$G iam service-accounts add-iam-policy-binding "${INVOKE_SA}" \
  --member="serviceAccount:service-${NUMBER}@gcp-sa-pubsub.iam.gserviceaccount.com" \
  --role=roles/iam.serviceAccountTokenCreator >/dev/null
if have $G pubsub subscriptions describe "${SUB}"; then
  echo "exists: ${SUB}"
else
  $G pubsub subscriptions create "${SUB}" --topic="${TOPIC}" \
    --push-endpoint="${URL}/events/gmail" \
    --push-auth-service-account="${INVOKE_SA}" --push-auth-token-audience="${URL}" \
    --ack-deadline=60 --message-retention-duration=10m --expiration-period=never
  echo "created: ${SUB}"
fi

say "4/5 scheduler jobs (daily)"
job() {  # name, schedule, path
  if have $G scheduler jobs describe "$1" --location="${REGION}"; then
    echo "exists: $1"
  else
    $G scheduler jobs create http "$1" --location="${REGION}" --schedule="$2" \
      --time-zone="${TIME_ZONE}" --http-method=POST --uri="${URL}$3" \
      --oidc-service-account-email="${INVOKE_SA}" --oidc-token-audience="${URL}" \
      --attempt-deadline=120s --max-retry-attempts=0
    echo "created: $1"
  fi
}
job "${SERVICE}-renew-watch" "17 6 * * *" "/cron/renew-watch"
job "${SERVICE}-watchdog" "43 6 * * *" "/cron/watchdog"

say "5/5 start listening now: renew the Gmail watch once"
$G scheduler jobs run "${SERVICE}-renew-watch" --location="${REGION}"
echo "renew-watch triggered; check /healthz from the deploy workflow"

say "summary"
echo "push:      $($G pubsub subscriptions describe "${SUB}" --format='value(pushConfig.pushEndpoint)')"
$G scheduler jobs list --location="${REGION}" --format="value(name,schedule,state)" --filter="name~${SERVICE}"
echo "DONE part 2"
