"""Shared wire format + traffic class table for the QoS traffic management system.

Imported by the UDP sender, the UDP receiver and the SDN controller so that the
application's notion of a "traffic class" and the network's notion of one can
never drift apart.
"""
import struct

# class_id | seq | send_time (unix epoch, float) | payload_len
HEADER = struct.Struct("!BIdH")
MAGIC_PAYLOAD = b"\xa5"

# Single source of truth. `queue` is the OVS HTB queue id the SDN controller
# steers the class into when the prioritised policy is active.
CLASSES = {
    "voice": {
        "id": 1, "port": 5001, "dscp": 46,   # EF
        "queue": 1, "rate_bps": 512_000, "pkt_size": 200,
        "min_rate": 2_000_000, "htb_prio": 0,
    },
    "video": {
        "id": 2, "port": 5002, "dscp": 34,   # AF41
        "queue": 2, "rate_bps": 3_000_000, "pkt_size": 1200,
        "min_rate": 5_000_000, "htb_prio": 1,
    },
    "bulk": {
        "id": 3, "port": 5003, "dscp": 0,    # BE
        "queue": 0, "rate_bps": 20_000_000, "pkt_size": 1400,
        "min_rate": 500_000, "htb_prio": 2,
    },
}

BY_ID = {c["id"]: name for name, c in CLASSES.items()}
BY_PORT = {c["port"]: name for name, c in CLASSES.items()}

# Bottleneck link capacity enforced by the OVS HTB root qdisc (bits/sec).
BOTTLENECK_BPS = 10_000_000

# TCP control channel the controller listens on for live policy changes.
CONTROL_HOST = "127.0.0.1"
CONTROL_PORT = 6900


def pack(class_id, seq, send_time, pkt_size):
    """Build one datagram of exactly `pkt_size` bytes (>= HEADER.size)."""
    pad = max(0, pkt_size - HEADER.size)
    return HEADER.pack(class_id, seq, send_time, pad) + MAGIC_PAYLOAD * pad


def unpack(datagram):
    """Return (class_id, seq, send_time) or raise ValueError on a bad packet."""
    if len(datagram) < HEADER.size:
        raise ValueError("short packet: %d bytes" % len(datagram))
    class_id, seq, send_time, pad = HEADER.unpack(datagram[:HEADER.size])
    if class_id not in BY_ID:
        raise ValueError("unknown class id %d" % class_id)
    if len(datagram) != HEADER.size + pad:
        raise ValueError("length mismatch: declared %d, got %d"
                         % (HEADER.size + pad, len(datagram)))
    return class_id, seq, send_time
