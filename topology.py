#!/usr/bin/env python3
"""Mininet topology + experiment driver for the QoS traffic management system.

        h1 (voice)  --\
        h2 (video)  ---  s1 ===== bottleneck 10 Mbps ===== s2 --- h4 (sink)
        h3 (bulk)   --/

The bottleneck is the OVS HTB root qdisc on s1-eth4, carved into three queues
that the SDN controller steers traffic into. Run it as root, with the
controller already running:

    sudo python3 topology.py --scenario all
    sudo python3 topology.py --cli            # interactive
"""
import argparse
import json
import os
import subprocess
import sys
import time

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qos_proto as P
import qosctl

HERE = os.path.dirname(os.path.abspath(__file__))
RESULTS = os.path.join(HERE, "results")
BOTTLENECK_IF = "s1-eth4"          # s1's port towards s2; port_no 4
SINK_IP = "10.0.0.4"


def build_net(controller_ip, controller_port):
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink,
                  autoSetMacs=True, cleanup=True)
    net.addController("c0", controller=RemoteController,
                      ip=controller_ip, port=controller_port)

    s1 = net.addSwitch("s1", protocols="OpenFlow13", dpid="0000000000000001")
    s2 = net.addSwitch("s2", protocols="OpenFlow13", dpid="0000000000000002")

    hosts = {}
    for idx, name in enumerate(("voice", "video", "bulk"), start=1):
        h = net.addHost("h%d" % idx, ip="10.0.0.%d/24" % idx)
        net.addLink(h, s1, delay="2ms")      # ports s1-eth1..3
        hosts[name] = h
    net.addLink(s1, s2, delay="5ms")         # s1-eth4 <-> s2-eth1  (bottleneck)

    sink = net.addHost("h4", ip=SINK_IP + "/24")
    net.addLink(sink, s2)                    # s2-eth2
    hosts["sink"] = sink
    return net, hosts


def _ovs(args):
    subprocess.run(["ovs-vsctl"] + args, check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)


def setup_queues(iface=BOTTLENECK_IF):
    """Create the HTB root + one queue per traffic class on the bottleneck.

    min-rate is the guarantee a class keeps under congestion; priority is the
    order in which HTB hands out the leftover capacity.
    """
    clear_queues(iface)
    by_queue = {c["queue"]: c for c in P.CLASSES.values()}
    qids = sorted(by_queue)
    cmd = ["--", "set", "port", iface, "qos=@newqos",
           "--", "--id=@newqos", "create", "qos", "type=linux-htb",
           "other-config:max-rate=%d" % P.BOTTLENECK_BPS,
           "queues=" + ",".join("%d=@q%d" % (q, q) for q in qids)]
    for q in qids:
        cfg = by_queue[q]
        cmd += ["--", "--id=@q%d" % q, "create", "queue",
                "other-config:min-rate=%d" % cfg["min_rate"],
                "other-config:max-rate=%d" % P.BOTTLENECK_BPS,
                "other-config:priority=%d" % cfg["htb_prio"]]
    _ovs(cmd)
    info("*** HTB queues on %s: %s\n" % (
        iface, {name: c["queue"] for name, c in P.CLASSES.items()}))


def clear_queues(iface=BOTTLENECK_IF):
    for args in (["--if-exists", "clear", "port", iface, "qos"],
                 ["--all", "destroy", "qos"],
                 ["--all", "destroy", "queue"]):
        subprocess.run(["ovs-vsctl"] + args,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def run_scenario(hosts, label, policy, duration, bulk_delay, event_log):
    """One measured run: set the policy, generate traffic, collect the report."""
    out = os.path.join(RESULTS, "%s.json" % label)
    try:
        info("*** control channel: %s\n" % json.dumps(qosctl.send("policy " + policy)))
    except OSError as exc:
        info("*** WARNING: control channel unreachable (%s); "
             "controller keeps its launch policy\n" % exc)

    if event_log:
        with open(event_log, "a") as fh:
            fh.write(json.dumps({"kind": "scenario_start", "label": label,
                                 "policy": policy, "t": round(time.time(), 3)}) + "\n")

    py = sys.executable or "python3"
    sink = hosts["sink"]
    rx = sink.popen([py, os.path.join(HERE, "udp_receiver.py"),
                     "--duration", str(duration + 3),
                     "--label", label, "--out", out],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    time.sleep(1.0)        # let the sockets bind before traffic starts

    senders = []
    for name in ("voice", "video", "bulk"):
        delay = bulk_delay if name == "bulk" else 0.0
        senders.append(hosts[name].popen(
            [py, os.path.join(HERE, "udp_sender.py"),
             "--class", name, "--dst", SINK_IP,
             "--duration", str(duration - delay),
             "--start-delay", str(delay),
             "--out", os.path.join(RESULTS, "%s-%s-tx.json" % (label, name))],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT))

    info("*** [%s] policy=%s running %.0fs (bulk joins at t=%.0fs)\n"
         % (label, policy, duration, bulk_delay))
    for p in senders:
        p.wait()
    rx_out, _ = rx.communicate()
    sys.stdout.write(rx_out.decode(errors="replace"))

    if event_log:
        with open(event_log, "a") as fh:
            fh.write(json.dumps({"kind": "scenario_end", "label": label,
                                 "t": round(time.time(), 3)}) + "\n")
    return out


SCENARIOS = {
    # label        policy pinned on the controller before traffic starts
    "baseline": "flat",    # static network: every class in the best-effort queue
    "qos": "qos",          # prioritisation on for the whole run
    "dynamic": "auto",     # controller flips the policy when it sees congestion
}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--scenario", default="all",
                    choices=sorted(SCENARIOS) + ["all"])
    ap.add_argument("--duration", type=float, default=30.0)
    ap.add_argument("--bulk-delay", type=float, default=10.0,
                    help="seconds before the bulk class starts (creates congestion)")
    ap.add_argument("--controller-ip", default="127.0.0.1")
    ap.add_argument("--controller-port", type=int, default=6653)
    ap.add_argument("--cli", action="store_true",
                    help="drop into the Mininet CLI instead of measuring")
    args = ap.parse_args()

    if os.geteuid() != 0:
        sys.exit("Mininet needs root: sudo python3 topology.py ...")
    os.makedirs(RESULTS, exist_ok=True)

    setLogLevel("info")
    net, hosts = build_net(args.controller_ip, args.controller_port)
    net.start()
    try:
        setup_queues()
        info("*** warming up the learning switch\n")
        net.pingAll(timeout="1")

        if args.cli:
            CLI(net)
            return

        order = sorted(SCENARIOS) if args.scenario == "all" else [args.scenario]
        if args.scenario == "all":
            order = ["baseline", "dynamic", "qos"]
        produced = []
        for label in order:
            event_log = os.path.join(RESULTS, "events-%s.jsonl" % label)
            open(event_log, "w").close()
            produced.append(run_scenario(hosts, label, SCENARIOS[label],
                                         args.duration, args.bulk_delay,
                                         event_log))
            time.sleep(2.0)

        info("*** results: %s\n" % ", ".join(produced))
        info("*** now run:  python3 analyze.py %s\n" % " ".join(produced))
    finally:
        clear_queues()
        net.stop()


if __name__ == "__main__":
    main()
