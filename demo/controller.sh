#!/usr/bin/env bash
# Terminal 1 of the live demo: start the QoS controller in auto mode.
# Leave it running and watch it print congestion and policy events.
#     ./demo/controller.sh
set -uo pipefail
cd "$(dirname "$0")/.."

MANAGER=""
for cand in ryu-manager osken-manager; do
    command -v "$cand" >/dev/null 2>&1 && { MANAGER="$cand"; break; }
done
[ -n "$MANAGER" ] || { echo "no ryu-manager/osken-manager found; run ./setup.sh" >&2; exit 1; }

echo "*** starting $MANAGER in auto mode (events also go to /tmp/demo-events.jsonl)"
exec env QOS_MODE=auto QOS_EVENT_LOG=/tmp/demo-events.jsonl \
    "$MANAGER" --ofp-tcp-listen-port 6653 qos_controller.py
