#!/usr/bin/env python3
"""Self-check: wire format, class table, loopback send/receive, fairness math.

    python3 test_qos.py        # no root, no Mininet needed
"""
import socket
import threading
import time

import qos_proto as P
import udp_receiver
import analyze


def test_wire_format():
    for name, cfg in P.CLASSES.items():
        d = P.pack(cfg["id"], 1234, 1700000000.5, cfg["pkt_size"])
        assert len(d) == cfg["pkt_size"], (name, len(d))
        cid, seq, ts = P.unpack(d)
        assert (cid, seq, ts) == (cfg["id"], 1234, 1700000000.5)

    for bad in (b"", b"\x01\x02", P.pack(1, 0, 0.0, 100)[:-1],
                b"\x7f" + P.pack(1, 0, 0.0, 100)[1:]):
        try:
            P.unpack(bad)
        except ValueError:
            continue
        raise AssertionError("accepted a malformed packet: %r" % bad[:16])


def test_class_table():
    for field in ("id", "port", "queue"):
        values = [c[field] for c in P.CLASSES.values()]
        assert len(set(values)) == len(values), "duplicate %s" % field
    assert sum(c["min_rate"] for c in P.CLASSES.values()) <= P.BOTTLENECK_BPS, \
        "HTB guarantees oversubscribe the bottleneck"
    assert sum(c["rate_bps"] for c in P.CLASSES.values()) > P.BOTTLENECK_BPS, \
        "offered load must exceed capacity or there is no congestion to manage"


def test_loopback_measurement():
    stop = threading.Event()
    sink = udp_receiver.ClassSink("voice", "127.0.0.1", stop)
    sink.start()

    cfg = P.CLASSES["voice"]
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sent = 50
    for seq in range(sent):
        if seq == 7:
            continue                      # simulate one lost datagram
        s.sendto(P.pack(cfg["id"], seq, time.time(), cfg["pkt_size"]),
                 ("127.0.0.1", cfg["port"]))
        time.sleep(0.002)
    s.sendto(b"garbage", ("127.0.0.1", cfg["port"]))
    time.sleep(0.5)
    stop.set()
    sink.join(timeout=2.0)
    s.close()

    st = sink.stats()
    assert st["received"] == sent - 1, st["received"]
    assert st["expected"] == sent, st["expected"]
    assert st["lost"] == 1, st["lost"]
    assert abs(st["loss_pct"] - 2.0) < 0.01, st["loss_pct"]
    assert st["malformed"] == 1, st["malformed"]
    assert st["bytes"] == (sent - 1) * cfg["pkt_size"]
    assert 0 <= st["latency_ms"]["avg"] < 1000, st["latency_ms"]
    assert st["timeline"], "per-second timeline is empty"


def test_fairness_index():
    assert abs(analyze.jain([5, 5, 5]) - 1.0) < 1e-9
    assert abs(analyze.jain([10, 0, 0]) - 1.0 / 3) < 1e-9
    assert analyze.jain([]) == 0.0 and analyze.jain([0, 0]) == 0.0
    assert analyze.jain([8, 1, 1]) < analyze.jain([4, 3, 3])


if __name__ == "__main__":
    for fn in (test_wire_format, test_class_table,
               test_loopback_measurement, test_fairness_index):
        fn()
        print("ok  %s" % fn.__name__)
    print("all checks passed")
