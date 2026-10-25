#!/usr/bin/env bash
# One-time setup on Ubuntu (tested on 20.04 / 22.04 / 24.04).
set -euo pipefail

sudo apt-get update
sudo apt-get install -y mininet openvswitch-switch python3-pip python3-venv
sudo systemctl enable --now openvswitch-switch

# Ryu is unmaintained and breaks on Python >= 3.10; os-ken is the maintained
# fork with the same API, and the controller imports whichever is present.
if python3 -c 'import sys; sys.exit(0 if sys.version_info < (3,10) else 1)'; then
    pip3 install --user "ryu==4.34" "eventlet==0.30.2"
else
    pip3 install --user --break-system-packages os-ken || pip3 install --user os-ken
fi

# Optional: throughput-over-time plot in the report.
pip3 install --user --break-system-packages matplotlib 2>/dev/null \
    || pip3 install --user matplotlib 2>/dev/null || true

echo
echo "Setup done. Verify with:  python3 test_qos.py  &&  sudo ./run.sh"
