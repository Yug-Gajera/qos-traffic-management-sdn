#!/usr/bin/env python3
"""Concurrent UDP sink: one thread per traffic class, one socket per class port.

Measures per-class throughput, one-way latency, jitter and packet loss, both as
a run total and in 1-second buckets so the effect of a mid-run policy change is
visible in the timeline.

    ./udp_receiver.py --duration 40 --out results/qos.json
"""
import argparse
import json
import math
import os
import socket
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qos_proto as P


class ClassSink(threading.Thread):
    """Receives one traffic class and accumulates its statistics."""

    def __init__(self, class_name, bind_ip, stop_event):
        super().__init__(name="sink-" + class_name, daemon=True)
        self.class_name = class_name
        self.cfg = P.CLASSES[class_name]
        self.stop_event = stop_event
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4 << 20)
        self.sock.bind((bind_ip, self.cfg["port"]))
        self.sock.settimeout(0.5)

        self.packets = 0
        self.bytes = 0
        self.bad = 0
        self.max_seq = -1
        self.latency_sum = 0.0
        self.latency_sq_sum = 0.0
        self.latency_min = float("inf")
        self.latency_max = 0.0
        self.prev_latency = None
        self.jitter = 0.0          # RFC 3550 smoothed interarrival jitter
        self.first_rx = None
        self.last_rx = None
        self.buckets = {}          # second offset -> [packets, bytes, lat_sum]

    def run(self):
        while not self.stop_event.is_set():
            try:
                data, _ = self.sock.recvfrom(65535)
            except socket.timeout:
                continue
            except OSError:
                break
            now = time.time()
            try:
                _, seq, sent_at = P.unpack(data)
            except ValueError:
                self.bad += 1
                continue

            if self.first_rx is None:
                self.first_rx = now
            self.last_rx = now
            self.packets += 1
            self.bytes += len(data)
            if seq > self.max_seq:
                self.max_seq = seq

            latency = now - sent_at
            if latency < 0:                 # clock skew guard
                latency = 0.0
            self.latency_sum += latency
            self.latency_sq_sum += latency * latency
            self.latency_min = min(self.latency_min, latency)
            self.latency_max = max(self.latency_max, latency)
            if self.prev_latency is not None:
                d = abs(latency - self.prev_latency)
                self.jitter += (d - self.jitter) / 16.0
            self.prev_latency = latency

            b = self.buckets.setdefault(int(now - self.first_rx), [0, 0, 0.0])
            b[0] += 1
            b[1] += len(data)
            b[2] += latency

        self.sock.close()

    def stats(self):
        n = self.packets
        expected = self.max_seq + 1 if self.max_seq >= 0 else 0
        span = (self.last_rx - self.first_rx) if n > 1 else 0.0
        mean = self.latency_sum / n if n else 0.0
        var = (self.latency_sq_sum / n - mean * mean) if n else 0.0
        timeline = [
            {"t": t, "mbps": round(v[1] * 8 / 1e6, 4), "packets": v[0],
             "avg_latency_ms": round(v[2] / v[0] * 1000, 3)}
            for t, v in sorted(self.buckets.items())
        ]
        return {
            "class": self.class_name,
            "udp_port": self.cfg["port"],
            "dscp": self.cfg["dscp"],
            "received": n,
            "expected": expected,
            "lost": max(0, expected - n),
            "loss_pct": round(100.0 * max(0, expected - n) / expected, 3) if expected else 0.0,
            "malformed": self.bad,
            "bytes": self.bytes,
            "duration_s": round(span, 3),
            "throughput_mbps": round(self.bytes * 8 / span / 1e6, 4) if span > 0 else 0.0,
            "latency_ms": {
                "avg": round(mean * 1000, 3),
                "min": round(self.latency_min * 1000, 3) if n else 0.0,
                "max": round(self.latency_max * 1000, 3),
                "stdev": round(math.sqrt(max(var, 0.0)) * 1000, 3),
            },
            "jitter_ms": round(self.jitter * 1000, 3),
            "timeline": timeline,
        }


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--duration", type=float, default=40.0)
    ap.add_argument("--out", default=None)
    ap.add_argument("--label", default="run")
    args = ap.parse_args()

    stop = threading.Event()
    sinks = [ClassSink(name, args.bind, stop) for name in sorted(P.CLASSES)]
    for s in sinks:
        s.start()
    print("[receiver] listening on ports %s for %.0fs"
          % (sorted(c["port"] for c in P.CLASSES.values()), args.duration), flush=True)

    try:
        time.sleep(args.duration)
    except KeyboardInterrupt:
        pass
    stop.set()
    for s in sinks:
        s.join(timeout=2.0)

    report = {"label": args.label, "generated_at": time.time(),
              "classes": {s.class_name: s.stats() for s in sinks}}
    for name, st in report["classes"].items():
        print("[receiver] %-6s %7.3f Mbps  loss %5.2f%%  lat %7.2f ms  jitter %5.2f ms"
              % (name, st["throughput_mbps"], st["loss_pct"],
                 st["latency_ms"]["avg"], st["jitter_ms"]), flush=True)

    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, "w") as fh:
            json.dump(report, fh, indent=2)
        print("[receiver] wrote %s" % args.out, flush=True)


if __name__ == "__main__":
    main()
