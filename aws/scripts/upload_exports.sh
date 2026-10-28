#!/usr/bin/env bash
# Upload the current Power BI exports as one immutable ingestion batch, manifest LAST.
#   GL_BUCKET=<bucket> aws/scripts/upload_exports.sh [--run-date YYYY-MM-DD] [--batch-id ID] [--source DIR]
# Requires: AWS CLI v2 with a signed-in profile (aws sso login), Python 3.11 (project venv).
# Never uses --delete: earlier batches are never removed or overwritten.
set -euo pipefail
: "${GL_BUCKET:?set GL_BUCKET to your bucket name}"
REGION="${GL_REGION:-ap-south-1}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${PYTHON:-python}"

BATCH_DIR="$("$PY" "$ROOT/aws/scripts/build_batch.py" --print-dir "$@")"
RUN_PART="$(basename "$(dirname "$BATCH_DIR")")"   # run_date=YYYY-MM-DD
BATCH_PART="$(basename "$BATCH_DIR")"              # batch=<id>
KEY="raw/powerbi/${RUN_PART}/${BATCH_PART}"
DEST="s3://${GL_BUCKET}/${KEY}/"

if aws s3api head-object --bucket "$GL_BUCKET" --key "${KEY}/_manifest.json" --region "$REGION" >/dev/null 2>&1; then
  echo "ERROR: ${DEST} already has a manifest; batches are immutable (choose a new --batch-id)." >&2
  exit 1
fi

echo "Uploading CSVs to ${DEST}"
aws s3 sync "$BATCH_DIR" "$DEST" --exclude "_manifest.json" --only-show-errors --region "$REGION"
echo "Uploading the manifest last (this starts the pipeline)"
aws s3 cp "$BATCH_DIR/_manifest.json" "${DEST}_manifest.json" --only-show-errors --region "$REGION"
echo "Done: ingestion ${RUN_PART#run_date=}_${BATCH_PART#batch=}"
