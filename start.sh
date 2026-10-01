#!/bin/sh
set -eu
cd "$(dirname "$0")"
mkdir -p runs
stamp=$(date +%Y%m%d-%H%M%S)
exec .venv/bin/python run_controller.py --profile calibration/profile.json --execute --record-route --seconds "${1:-120}" --log "runs/session-$stamp.jsonl"
