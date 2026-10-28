#!/usr/bin/env bash
# Start the Glue ETL manually for a batch that is already in S3 (recovery path; bypasses the Lambda lock).
#   GL_BUCKET=<bucket> aws/scripts/run_etl.sh <run_date> <batch_id> [--wait]
# Safe to repeat: the ingestion id is derived from run_date + batch_id, so a re-run overwrites the same
# curated location with the same output and republishes it.
set -euo pipefail
: "${GL_BUCKET:?set GL_BUCKET}"
RUN_DATE="${1:?run_date (YYYY-MM-DD)}"
BATCH_ID="${2:?batch_id}"
WAIT="${3:-}"
REGION="${GL_REGION:-ap-south-1}"
JOB="${GL_GLUE_JOB:-grievance-lens-etl}"
B="s3://${GL_BUCKET}"
ARGS=$(printf '{"--run_date":"%s","--batch_id":"%s","--raw_prefix":"%s/raw/powerbi/run_date=%s/batch=%s/","--curated_root":"%s/curated/","--control_root":"%s/control/","--contract_uri":"%s/scripts/contract.json","--publish":"true"}' \
  "$RUN_DATE" "$BATCH_ID" "$B" "$RUN_DATE" "$BATCH_ID" "$B" "$B" "$B")
RUN_ID="$(aws glue start-job-run --job-name "$JOB" --arguments "$ARGS" --region "$REGION" --query JobRunId --output text)"
echo "Started $JOB run $RUN_ID for ${RUN_DATE}_${BATCH_ID}"
if [[ "$WAIT" == "--wait" ]]; then
  while true; do
    STATE="$(aws glue get-job-run --job-name "$JOB" --run-id "$RUN_ID" --region "$REGION" --query JobRun.JobRunState --output text)"
    echo "  $STATE"
    case "$STATE" in
      SUCCEEDED) exit 0 ;;
      FAILED|ERROR|TIMEOUT|STOPPED) exit 1 ;;
    esac
    sleep 20
  done
fi
