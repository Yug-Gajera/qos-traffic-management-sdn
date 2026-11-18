#!/usr/bin/env bash
# Terminal 3 of the live demo: show what the controller and the switch are doing.
#     ./demo/check.sh
# Run it before bulk joins (flat: everything in queue 0) and again after
# (qos: voice in queue 1, video in queue 2, bulk in queue 0).
cd "$(dirname "$0")/.."

echo "=== controller state (qosctl.py status) ==="
python3 qosctl.py status

echo
echo "=== classification rules on s1, table 0 (set_queue per UDP port) ==="
sudo ovs-ofctl -O OpenFlow13 dump-flows s1 table=0 \
    || echo "(no switch s1: is 'sudo python3 topology.py --cli' still running?)"

echo
echo "=== HTB queues on the bottleneck s1-eth4 ==="
echo "    1:1 = queue 0 (bulk)   1:2 = queue 1 (voice)   1:3 = queue 2 (video)"
sudo tc -s class show dev s1-eth4 \
    || echo "(no s1-eth4: is 'sudo python3 topology.py --cli' still running?)"
