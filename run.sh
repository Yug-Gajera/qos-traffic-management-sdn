#!/usr/bin/env bash
# Full experiment: start the SDN controller, run every scenario in Mininet,
# then produce the report.  Usage:  sudo ./run.sh [--duration 30]
set -uo pipefail
cd "$(dirname "$0")"

if [ "$(id -u)" -ne 0 ]; then
    echo "run as root: sudo ./run.sh $*" >&2; exit 1
fi

MANAGER=""
for cand in ryu-manager osken-manager; do
    command -v "$cand" >/dev/null 2>&1 && { MANAGER="$cand"; break; }
    [ -x "$HOME/.local/bin/$cand" ] && { MANAGER="$HOME/.local/bin/$cand"; break; }
done
[ -n "$MANAGER" ] || { echo "no ryu-manager/osken-manager found; run ./setup.sh" >&2; exit 1; }

mkdir -p results
echo "*** cleaning up any previous Mininet state"
mn -c >/dev/null 2>&1

echo "*** starting controller ($MANAGER)"
QOS_MODE=auto QOS_EVENT_LOG="$PWD/results/controller.jsonl" \
    "$MANAGER" --ofp-tcp-listen-port 6653 qos_controller.py \
    > results/controller.log 2>&1 &
CONTROLLER_PID=$!
trap 'kill $CONTROLLER_PID 2>/dev/null; mn -c >/dev/null 2>&1' EXIT

for _ in $(seq 1 30); do
    (echo >/dev/tcp/127.0.0.1/6653) >/dev/null 2>&1 && break
    sleep 0.5
done
(echo >/dev/tcp/127.0.0.1/6653) >/dev/null 2>&1 \
    || { echo "controller failed to start; see results/controller.log" >&2;
         tail -20 results/controller.log >&2; exit 1; }
echo "*** controller listening on 6653 (log: results/controller.log)"

python3 topology.py --scenario all "$@"
RC=$?

echo "*** analysing"
python3 analyze.py
exit $RC
