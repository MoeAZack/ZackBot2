#!/usr/bin/env bash
# Kept for compatibility: the full verification is now `python3 verify.py full` (summary: dev_out/verify/latest_full.json).
set -euo pipefail
cd "$(dirname "$0")"
exec python3 verify.py full "$@"
