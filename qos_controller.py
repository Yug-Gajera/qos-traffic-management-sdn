#!/usr/bin/env python3
"""SDN controller: dynamic QoS prioritisation for UDP traffic classes.

Pipeline (OpenFlow 1.3):

    table 0  classification -- match the class's UDP port, apply set_queue,
                               goto table 1
    table 1  forwarding     -- plain MAC-learning switch

Keeping classification in its own table means a policy change is three flow-mod
messages to table 0; the learning state in table 1 is never disturbed.

The controller polls port statistics on the bottleneck link. When utilisation
crosses the high-water mark it switches the policy from 'flat' (everything in
the best-effort queue -- the static baseline) to 'qos' (each class steered into
its own HTB queue), and back again when the congestion drains. A small TCP
control channel allows the policy and the per-class queue mapping to be changed
while traffic is running.

    ryu-manager qos_controller.py
    QOS_MODE=flat ryu-manager qos_controller.py      # static baseline
"""
import importlib
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import qos_proto as P


def _framework():
    """Ryu is unmaintained on recent Python; os-ken is the drop-in fork."""
    for pkg in ("ryu", "os_ken"):
        try:
            importlib.import_module(pkg)
            return pkg
        except ImportError:
            continue
    raise ImportError("install one of: ryu, os-ken")


FW = _framework()
_mod = lambda sub: importlib.import_module(FW + "." + sub)

app_manager = _mod("base.app_manager")
ofp_event = _mod("controller.ofp_event")
handler = _mod("controller.handler")
ofproto_v1_3 = _mod("ofproto.ofproto_v1_3")
hub = _mod("lib.hub")
packet = _mod("lib.packet.packet")
ethernet = _mod("lib.packet.ethernet")
ether_types = _mod("lib.packet.ether_types")

TABLE_CLASSIFY = 0
TABLE_FORWARD = 1

# Bottleneck to watch: switch dpid and its egress port number.
BOTTLENECK_DPID = int(os.environ.get("QOS_BOTTLENECK_DPID", "1"))
BOTTLENECK_PORT = int(os.environ.get("QOS_BOTTLENECK_PORT", "4"))
POLL_INTERVAL = float(os.environ.get("QOS_POLL_INTERVAL", "2.0"))
HIGH_WATER = float(os.environ.get("QOS_HIGH_WATER", "0.80"))   # of link capacity
LOW_WATER = float(os.environ.get("QOS_LOW_WATER", "0.60"))
EVENT_LOG = os.environ.get("QOS_EVENT_LOG", "")

BEST_EFFORT_QUEUE = 0


class QoSController(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    def __init__(self, *args, **kwargs):
        super(QoSController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self.datapaths = {}
        # 'auto' reacts to congestion; 'qos'/'flat' pin the policy for A/B runs.
        self.mode = os.environ.get("QOS_MODE", "auto").lower()
        self.policy = "qos" if self.mode == "qos" else "flat"
        self.queue_map = {name: cfg["queue"] for name, cfg in P.CLASSES.items()}
        self.last_port_bytes = {}
        self.last_poll_time = None
        self.utilisation = 0.0
        self.congested = False
        self.events = []
        self.logger.info("QoS controller up (framework=%s mode=%s policy=%s)",
                         FW, self.mode, self.policy)
        self.monitor_thread = hub.spawn(self._monitor_loop)
        self.control_thread = hub.spawn(self._control_loop)

    # ------------------------------------------------------------------ utils

    def _event(self, kind, **fields):
        rec = dict(kind=kind, t=round(time.time(), 3), **fields)
        self.events.append(rec)
        self.logger.info("EVENT %s", json.dumps(rec))
        if EVENT_LOG:
            try:
                with open(EVENT_LOG, "a") as fh:
                    fh.write(json.dumps(rec) + "\n")
            except OSError as exc:
                self.logger.warning("event log write failed: %s", exc)

    def _add_flow(self, dp, table, priority, match, instructions,
                  idle=0, hard=0):
        mod = dp.ofproto_parser.OFPFlowMod(
            datapath=dp, table_id=table, priority=priority, match=match,
            instructions=instructions, idle_timeout=idle, hard_timeout=hard)
        dp.send_msg(mod)

    # ------------------------------------------------------------- switch init

    @handler.set_ev_cls(ofp_event.EventOFPSwitchFeatures, handler.CONFIG_DISPATCHER)
    def _features(self, ev):
        dp = ev.msg.datapath
        parser = dp.ofproto_parser
        ofp = dp.ofproto
        self.datapaths[dp.id] = dp

        # table 0 miss -> fall through to forwarding
        self._add_flow(dp, TABLE_CLASSIFY, 0, parser.OFPMatch(),
                       [parser.OFPInstructionGotoTable(TABLE_FORWARD)])
        # table 1 miss -> ask the controller
        self._add_flow(dp, TABLE_FORWARD, 0, parser.OFPMatch(),
                       [parser.OFPInstructionActions(
                           ofp.OFPIT_APPLY_ACTIONS,
                           [parser.OFPActionOutput(ofp.OFPP_CONTROLLER,
                                                   ofp.OFPCML_NO_BUFFER)])])
        self._apply_policy(dp)
        self.logger.info("switch %016x connected", dp.id)

    @handler.set_ev_cls(ofp_event.EventOFPStateChange,
                        [handler.MAIN_DISPATCHER, handler.DEAD_DISPATCHER])
    def _state_change(self, ev):
        dp = ev.datapath
        if ev.state == handler.MAIN_DISPATCHER:
            self.datapaths[dp.id] = dp
        elif dp.id in self.datapaths:
            del self.datapaths[dp.id]
            self.logger.info("switch %016x disconnected", dp.id)

    # -------------------------------------------------------- classification

    def _apply_policy(self, dp=None):
        """(Re)write table 0 so each class lands in the queue the policy says."""
        targets = [dp] if dp is not None else list(self.datapaths.values())
        for datapath in targets:
            parser = datapath.ofproto_parser
            ofp = datapath.ofproto
            for name, cfg in P.CLASSES.items():
                queue = self.queue_map[name] if self.policy == "qos" \
                    else BEST_EFFORT_QUEUE
                match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP,
                                        ip_proto=17, udp_dst=cfg["port"])
                inst = [
                    parser.OFPInstructionActions(
                        ofp.OFPIT_APPLY_ACTIONS,
                        [parser.OFPActionSetQueue(queue)]),
                    parser.OFPInstructionGotoTable(TABLE_FORWARD),
                ]
                self._add_flow(datapath, TABLE_CLASSIFY, 10, match, inst)

    def set_policy(self, policy, reason="manual"):
        if policy == self.policy:
            return False
        self.policy = policy
        self._apply_policy()
        self._event("policy_change", policy=policy, reason=reason,
                    utilisation=round(self.utilisation, 3),
                    queues=dict(self.queue_map))
        return True

    # ------------------------------------------------------- forwarding plane

    @handler.set_ev_cls(ofp_event.EventOFPPacketIn, handler.MAIN_DISPATCHER)
    def _packet_in(self, ev):
        msg = ev.msg
        dp = msg.datapath
        parser = dp.ofproto_parser
        ofp = dp.ofproto
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        self.mac_to_port.setdefault(dp.id, {})[eth.src] = in_port
        out_port = self.mac_to_port[dp.id].get(eth.dst, ofp.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]

        if out_port != ofp.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_dst=eth.dst,
                                    eth_src=eth.src)
            self._add_flow(dp, TABLE_FORWARD, 1, match,
                           [parser.OFPInstructionActions(
                               ofp.OFPIT_APPLY_ACTIONS, actions)],
                           idle=30)

        data = msg.data if msg.buffer_id == ofp.OFP_NO_BUFFER else None
        dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                        in_port=in_port, actions=actions,
                                        data=data))

    # ------------------------------------------------------------- monitoring

    def _monitor_loop(self):
        while True:
            for dp in list(self.datapaths.values()):
                parser = dp.ofproto_parser
                dp.send_msg(parser.OFPPortStatsRequest(dp, 0,
                                                       dp.ofproto.OFPP_ANY))
            hub.sleep(POLL_INTERVAL)

    @handler.set_ev_cls(ofp_event.EventOFPPortStatsReply, handler.MAIN_DISPATCHER)
    def _port_stats(self, ev):
        dpid = ev.msg.datapath.id
        now = time.time()
        for stat in ev.msg.body:
            key = (dpid, stat.port_no)
            prev = self.last_port_bytes.get(key)
            self.last_port_bytes[key] = (now, stat.tx_bytes)
            if prev is None:
                continue
            dt = now - prev[0]
            if dt <= 0:
                continue
            bps = (stat.tx_bytes - prev[1]) * 8 / dt
            if dpid == BOTTLENECK_DPID and stat.port_no == BOTTLENECK_PORT:
                self.utilisation = bps / float(P.BOTTLENECK_BPS)
                self._react(bps)

    def _react(self, bps):
        """Congestion detection with hysteresis; only 'auto' mode acts on it."""
        util = self.utilisation
        if not self.congested and util >= HIGH_WATER:
            self.congested = True
            self._event("congestion_detected", mbps=round(bps / 1e6, 3),
                        utilisation=round(util, 3))
            if self.mode == "auto":
                self.set_policy("qos", reason="congestion")
        elif self.congested and util <= LOW_WATER:
            self.congested = False
            self._event("congestion_cleared", mbps=round(bps / 1e6, 3),
                        utilisation=round(util, 3))
            if self.mode == "auto":
                self.set_policy("flat", reason="congestion_cleared")

    # --------------------------------------------------------- control socket

    def _control_loop(self):
        """Line-oriented TCP control channel for live policy changes."""
        server = hub.StreamServer((P.CONTROL_HOST, P.CONTROL_PORT),
                                  self._handle_control)
        self.logger.info("control channel on %s:%d",
                         P.CONTROL_HOST, P.CONTROL_PORT)
        server.serve_forever()

    def _handle_control(self, sock, addr):
        fh = sock.makefile("rw")
        try:
            for line in fh:
                reply = self._command(line.strip())
                fh.write(json.dumps(reply) + "\n")
                fh.flush()
        except (OSError, ValueError):
            pass
        finally:
            try:
                fh.close()
                sock.close()
            except OSError:
                pass

    def _command(self, line):
        parts = line.split()
        if not parts:
            return {"ok": False, "error": "empty command"}
        cmd = parts[0].lower()

        if cmd == "status":
            return {"ok": True, "mode": self.mode, "policy": self.policy,
                    "queues": self.queue_map, "congested": self.congested,
                    "utilisation": round(self.utilisation, 3),
                    "switches": sorted(self.datapaths)}
        if cmd == "events":
            return {"ok": True, "events": self.events[-50:]}
        if cmd == "policy":
            if len(parts) != 2 or parts[1] not in ("qos", "flat", "auto"):
                return {"ok": False, "error": "usage: policy qos|flat|auto"}
            target = parts[1]
            if target == "auto":
                # Reset to the unprioritised state so an auto run always starts
                # from the baseline and the congestion trigger is observable.
                self.mode = "auto"
                self.congested = False
                self.set_policy("flat", reason="auto_reset")
                return {"ok": True, "mode": "auto", "policy": self.policy}
            self.mode = target
            changed = self.set_policy(target, reason="control_channel")
            return {"ok": True, "mode": self.mode, "policy": self.policy,
                    "changed": changed}
        if cmd == "set":
            if len(parts) != 3 or parts[1] not in P.CLASSES:
                return {"ok": False,
                        "error": "usage: set <%s> <queue>" % "|".join(sorted(P.CLASSES))}
            try:
                queue = int(parts[2])
            except ValueError:
                return {"ok": False, "error": "queue must be an integer"}
            self.queue_map[parts[1]] = queue
            self._apply_policy()
            self._event("queue_remap", traffic_class=parts[1], queue=queue)
            return {"ok": True, "queues": self.queue_map, "policy": self.policy}
        return {"ok": False, "error": "unknown command: %s" % cmd}
