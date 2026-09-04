#!/usr/bin/env bash
#
# Create (or update) the Cloud Scheduler jobs that auto-publish QC shifts.
#
# Idempotent: re-run it after changing a time or adding a shift and it updates
# the existing jobs in place rather than duplicating them.
#
# The jobs authenticate with an OIDC token from a dedicated service account,
# which main.py accepts for /api/shifts/auto-publish only (see
# _oidc_service_account_identity). No long-lived credential is stored anywhere.
#
# Publishing is still gated by the "Automatic shift assignment" switch in
# Settings — with it off, every run is a no-op that returns {"skipped": true}.
# The jobs can safely exist before you turn the switch on.
#
# Usage:  bash setup-scheduler.sh            # create/update all jobs
#         DRY_RUN=1 bash setup-scheduler.sh  # print the commands only
set -euo pipefail

PROJECT="${PROJECT:-storesight-internal-tools}"
REGION="${REGION:-us-central1}"
SERVICE_URL="${SERVICE_URL:-https://qc-shift-assignments.storesight.org}"
SA_NAME="qc-shift-scheduler"
SA_EMAIL="${SA_NAME}@${PROJECT}.iam.gserviceaccount.com"

# ---------------------------------------------------------------------------
# TIMEZONE — every shift time below is expressed in this zone.
# The QC team is in Arkansas, so shifts are US Central; America/Chicago is the
# IANA zone for that and handles CST/CDT automatically. Override only if the
# team's location changes.
# ---------------------------------------------------------------------------
TZ_NAME="${TZ_NAME:-America/Chicago}"

# Weekdays only. Use "*" for every day.
DAYS="${DAYS:-1-5}"

# One entry per shift: "<job-suffix>|<cron minute> <cron hour>|<shift_time>"
#
# `shift_time` must match the value the UI sends verbatim (see AssignMenu.tsx) —
# it's compared against Team Scheduler's shift labels, en-dash included.
#
# The two 1:00 PM shifts fire two minutes apart on purpose. Auto-publish writes
# a new snapshot and rewrites the reviewer_shift docs for everyone in its batch;
# two of them landing at the same instant would race over that shared state.
JOBS=(
  "0800|0 8|8:00 AM – 2:00 PM"
  "0830|30 8|8:30 AM – 12:00 PM"
  "1300|0 13|1:00 PM – 5:00 PM"
  "1302|2 13|1:00 PM – 6:00 PM"
)

run() {
  if [[ -n "${DRY_RUN:-}" ]]; then
    printf '  + %q ' "$@"; echo
  else
    "$@"
  fi
}

echo "Project:  $PROJECT"
echo "Region:   $REGION"
echo "Service:  $SERVICE_URL"
echo "Timezone: $TZ_NAME   (days: $DAYS)"
echo

# --- 1. Service account the scheduler mints its OIDC token as ---------------
if gcloud iam service-accounts describe "$SA_EMAIL" --project "$PROJECT" >/dev/null 2>&1; then
  echo "✓ service account exists: $SA_EMAIL"
else
  echo "→ creating service account $SA_EMAIL"
  run gcloud iam service-accounts create "$SA_NAME" \
    --project "$PROJECT" \
    --display-name "QC Shift Assignments scheduler" \
    --description "Mints OIDC tokens for the shift auto-publish Cloud Scheduler jobs"
fi

# The Cloud Run service is --allow-unauthenticated, so the app's own middleware
# does the authorization (allowlisting this SA). No run.invoker grant needed;
# add one here if the service is ever locked down at the IAM layer.

# --- 2. One scheduler job per shift ----------------------------------------
for entry in "${JOBS[@]}"; do
  IFS='|' read -r suffix cron shift_time <<< "$entry"
  job="qc-auto-publish-${suffix}"
  schedule="${cron} * * ${DAYS}"

  # Percent-encode the shift label (spaces and the en-dash) for the query string.
  encoded="$(python3 -c 'import sys,urllib.parse;print(urllib.parse.quote(sys.argv[1]))' "$shift_time")"
  uri="${SERVICE_URL}/api/shifts/auto-publish?shift_time=${encoded}"

  if gcloud scheduler jobs describe "$job" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1; then
    action=update
  else
    action=create
  fi

  echo "→ ${action} ${job}  ('${schedule}' ${TZ_NAME})  shift_time='${shift_time}'"
  run gcloud scheduler jobs "$action" http "$job" \
    --project "$PROJECT" \
    --location "$REGION" \
    --schedule "$schedule" \
    --time-zone "$TZ_NAME" \
    --uri "$uri" \
    --http-method POST \
    --oidc-service-account-email "$SA_EMAIL" \
    --oidc-token-audience "$SERVICE_URL" \
    --attempt-deadline 15m \
    --max-retry-attempts 3 \
    --min-backoff 30s \
    --description "Auto-publish QC shift assignments for the ${shift_time} shift"
done

# --- 3. End-of-day clear ----------------------------------------------------
# Wipes the current shift after the last one finishes, so reviewers don't open
# an empty-but-stale My Tasks in the evening. Points at /api/shifts/auto-clear,
# NOT /api/shifts/clear: the latter takes a `mode`, and a scheduled caller must
# never be able to reach mode="reset" (which deletes every shift across all
# time) or scope a clear to one reviewer. Gated by "Clear shifts at end of day"
# in Settings, so this job is a no-op until that switch is on.
#
# Runs every day rather than weekdays only: a shift published on a Friday would
# otherwise sit until Monday.
CLEAR_JOB="qc-shift-clear-eod"
CLEAR_SCHEDULE="${CLEAR_SCHEDULE:-30 17 * * *}"

if gcloud scheduler jobs describe "$CLEAR_JOB" --location "$REGION" --project "$PROJECT" >/dev/null 2>&1; then
  action=update
else
  action=create
fi

echo "→ ${action} ${CLEAR_JOB}  ('${CLEAR_SCHEDULE}' ${TZ_NAME})"
run gcloud scheduler jobs "$action" http "$CLEAR_JOB" \
  --project "$PROJECT" \
  --location "$REGION" \
  --schedule "$CLEAR_SCHEDULE" \
  --time-zone "$TZ_NAME" \
  --uri "${SERVICE_URL}/api/shifts/auto-clear" \
  --http-method POST \
  --oidc-service-account-email "$SA_EMAIL" \
  --oidc-token-audience "$SERVICE_URL" \
  --attempt-deadline 15m \
  --max-retry-attempts 3 \
  --min-backoff 30s \
  --description "Clear the finished QC shift at end of day"

echo
echo "Done. Verify with:"
echo "  gcloud scheduler jobs list --location $REGION --project $PROJECT"
echo
echo "Test one run now (a no-op until the Settings switch is on):"
echo "  gcloud scheduler jobs run qc-auto-publish-0800 --location $REGION --project $PROJECT"
