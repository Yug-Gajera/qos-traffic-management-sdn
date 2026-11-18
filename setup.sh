#!/usr/bin/env bash
# One-time setup on Ubuntu (tested on 20.04 / 22.04 / 24.04).
set -euo pipefail

sudo apt-get update
sudo apt-get install -y mininet openvswitch-switch python3-pip python3-venv \
    python3-matplotlib
sudo systemctl enable --now openvswitch-switch

# run.sh runs as root (sudo), so the controller framework is installed
# system-wide: a `pip install --user` package is invisible to root and its
# ~/.local/bin is not on sudo's PATH.
#
# Ryu is unmaintained and breaks on Python >= 3.10; os-ken is the maintained
# fork with the same API, and the controller imports whichever is present.
# os-ken 4.x dropped the osken-manager command, so pin the 3.x line.
if python3 -c 'import sys; sys.exit(0 if sys.version_info < (3,10) else 1)'; then
    sudo pip3 install "ryu==4.34" "eventlet==0.30.2"
else
    sudo pip3 install --break-system-packages "os-ken<4" \
        || sudo pip3 install "os-ken<4"
fi

echo
echo "Setup done. Verify with:  python3 test_qos.py  &&  sudo ./run.sh"
