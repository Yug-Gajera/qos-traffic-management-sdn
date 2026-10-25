#!/usr/bin/env python3
"""UDP traffic generator for one traffic class.

Paces datagrams at the class's configured rate, stamps each one with a sequence
number and a send timestamp, and marks the IP header with the class's DSCP so
the SDN controller can classify the flow in the data plane.

    ./udp_sender.py --class voice --dst 10.0.0.4 --duration 30
"""
import argparse
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qos_proto as P


def send(class_name, dst_ip, duration, rate_bps=None, out_path=None):
    cfg = P.CLASSES[class_name]
    rate_bps = rate_bps or cfg["rate_bps"]
    pkt_size = cfg["pkt_size"]
    interval = (pkt_size * 8) / float(rate_bps)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        # DSCP lives in the top 6 bits of the IP TOS byte.
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_TOS, cfg["dscp"] << 2)
    except OSError as exc:            # non-fatal: SDN also matches on UDP port
        print("[%s] could not set DSCP: %s" % (class_name, exc), file=sys.stderr)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF, 1 << 20)

    dst = (dst_ip, cfg["port"])
    start = time.time()
    deadline = start
    end = start + duration
    sent = errors = 0

    print("[%s] -> %s:%d  %.2f Mbps  %dB/pkt  %.0f pps  %ds"
          % (class_name, dst_ip, cfg["port"], rate_bps / 1e6, pkt_size,
             1.0 / interval, duration), flush=True)

    while True:
        now = time.time()
        if now >= end:
            break
        if deadline > now:
            time.sleep(min(deadline - now, end - now))
            continue
        try:
            sock.sendto(P.pack(cfg["id"], sent, time.time(), pkt_size), dst)
            sent += 1
        except OSError as exc:
            # ENOBUFS under heavy congestion is expected; count and keep going.
            errors += 1
            if errors < 5:
                print("[%s] send error: %s" % (class_name, exc), file=sys.stderr)
            time.sleep(0.001)
        deadline += interval
        if deadline < now - 1.0:      # fell badly behind: resync, don't burst
            deadline = now

    sock.close()
    elapsed = time.time() - start
    summary = {
        "class": class_name, "sent": sent, "send_errors": errors,
        "duration_s": round(elapsed, 3),
        "offered_mbps": round(sent * pkt_size * 8 / elapsed / 1e6, 4),
        "target_mbps": round(rate_bps / 1e6, 4),
    }
    print("[%s] sent=%d errors=%d offered=%.2f Mbps"
          % (class_name, sent, errors, summary["offered_mbps"]), flush=True)
    if out_path:
        with open(out_path, "w") as fh:
            json.dump(summary, fh, indent=2)
    return summary


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--class", dest="cls", required=True, choices=sorted(P.CLASSES))
    ap.add_argument("--dst", required=True, help="receiver IP address")
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--rate", type=float, default=None, help="override Mbps")
    ap.add_argument("--start-delay", type=float, default=0.0)
    ap.add_argument("--out", default=None, help="write JSON summary here")
    args = ap.parse_args()

    if args.start_delay:
        time.sleep(args.start_delay)
    send(args.cls, args.dst, args.duration,
         rate_bps=int(args.rate * 1e6) if args.rate else None,
         out_path=args.out)


if __name__ == "__main__":
    main()
