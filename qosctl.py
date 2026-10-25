#!/usr/bin/env python3
"""Tiny client for the controller's TCP control channel.

    ./qosctl.py status
    ./qosctl.py policy qos          # pin prioritised policy
    ./qosctl.py policy flat         # pin the static baseline
    ./qosctl.py policy auto         # react to congestion automatically
    ./qosctl.py set video 1         # remap a class to another queue, live
"""
import json
import socket
import sys

sys.path.insert(0, __file__.rsplit("/", 1)[0])
import qos_proto as P


def send(command, timeout=5.0):
    with socket.create_connection((P.CONTROL_HOST, P.CONTROL_PORT), timeout) as s:
        s.sendall((command + "\n").encode())
        buf = b""
        while not buf.endswith(b"\n"):
            chunk = s.recv(65535)
            if not chunk:
                break
            buf += chunk
    return json.loads(buf.decode().strip())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    try:
        print(json.dumps(send(" ".join(sys.argv[1:])), indent=2))
    except OSError as exc:
        sys.exit("control channel unreachable (%s:%d): %s"
                 % (P.CONTROL_HOST, P.CONTROL_PORT, exc))
