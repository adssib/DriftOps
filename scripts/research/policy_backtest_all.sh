#!/usr/bin/env bash
# Runs every retrain policy in the backtest, 4 at a time. ~15-25 minutes on 8 cores.
set -euo pipefail
cd "$(dirname "$0")/../.."
run() { uv run python scripts/research/policy_backtest.py --threads 2 "$@"; }
export -f run
cat <<'RUNS' | xargs -P 4 -I{} bash -c 'run {}'
--policy never --name never
--policy monthly --window 8w --no-gate --name monthly_8w_nogate
--policy monthly --window 8w --name monthly_8w_gated
--policy monthly --window 52w --name monthly_52w_gated
--policy driftops --window 8w --name driftops_8w
--policy driftops --window 52w --name driftops_52w
--policy never --corrupt distance_km --name corrupt_never
--policy driftops --window 8w --corrupt distance_km --name corrupt_driftops_guard
--policy driftops --window 8w --corrupt distance_km --no-guard --name corrupt_driftops_noguard
RUNS
