# QoS Traffic Management System (UDP sockets + SDN)

A UDP application that generates three classes of traffic, and an SDN
controller that classifies those flows and dynamically prioritises them when
the network becomes congested.

The whole system runs on Ubuntu inside Mininet: hosts run the real socket
application, Open vSwitch enforces the bandwidth limit and the per-class
queues, and a Ryu/os-ken controller decides which queue each flow goes into.

> **Requirements specification:** [`docs/SRS.md`](docs/SRS.md) — IEEE 830 SRS
> with numbered requirements and a traceability matrix mapping each one to the
> source file that implements it and the test case that verifies it.

---

## 1. Architecture

```
   h1 10.0.0.1  voice  (EF,  DSCP 46, UDP 5001, 0.5 Mbps,  200 B)  \
   h2 10.0.0.2  video  (AF41,DSCP 34, UDP 5002, 3.0 Mbps, 1200 B)  --  s1
   h3 10.0.0.3  bulk   (BE,  DSCP  0, UDP 5003, 20 Mbps,  1400 B)  /     |
                                                                         |  s1-eth4
                                        bottleneck: OVS HTB, 10 Mbps     |  (3 queues)
                                                                         |
                                                                        s2 -- h4 10.0.0.4 (sink)

   controller (OpenFlow 1.3, TCP 6653)     control channel (TCP 6900)
        |                                        ^
        +-- port stats poll every 2 s            +-- qosctl.py policy qos|flat|auto
```

**Data plane.** The bottleneck is the HTB root qdisc Open vSwitch installs on
`s1-eth4`, capped at 10 Mbps and split into three queues:

| queue | class | min-rate (guarantee) | HTB priority |
|---|---|---|---|
| 1 | voice | 2 Mbps | 0 (highest) |
| 2 | video | 5 Mbps | 1 |
| 0 | bulk  | 0.5 Mbps | 2 (lowest) |

**Control plane.** The controller uses a two-table OpenFlow pipeline:

* **table 0 — classification.** One flow per class matching
  `eth_type=IPv4, ip_proto=UDP, udp_dst=<class port>`, applying
  `set_queue(q)` and then `goto_table 1`.
* **table 1 — forwarding.** A plain MAC-learning switch.

Separating the two means a policy change is three flow-mods to table 0 and the
learned forwarding state is never touched.

**Offered load is 23.5 Mbps into a 10 Mbps link**, so congestion is guaranteed
and the queues actually matter.

## 2. Components

| File | Role |
|---|---|
| `qos_proto.py` | Wire format + the single traffic-class table shared by the app and the controller |
| `udp_sender.py` | Rate-paced UDP generator for one class; stamps seq + send time, sets the IP DSCP |
| `udp_receiver.py` | Concurrent sink, one thread and socket per class; measures throughput, loss, latency, jitter, per-second timeline |
| `qos_controller.py` | Ryu/os-ken app: classification pipeline, congestion detection from port stats, TCP control channel |
| `qosctl.py` | CLI client for the control channel (live policy changes) |
| `topology.py` | Mininet topology, OVS queue setup, experiment driver |
| `analyze.py` | Comparison tables, Jain fairness index, `results/report.md`, plot |
| `test_qos.py` | Self-check: wire format, class table, loopback measurement, fairness math |

### Application-level message format

```
 0        1                    5                              13          N
 +--------+--------------------+------------------------------+-----------+
 | class  |   sequence number  |  send timestamp (float64)     |  padding  |
 | (u8)   |      (u32 BE)      |  unix epoch seconds           |  to size  |
 +--------+--------------------+------------------------------+-----------+
```

The receiver rejects short datagrams, unknown class ids and length mismatches
(counted as `malformed`), derives loss from the sequence gap, latency from the
timestamp and jitter with the RFC 3550 smoothed estimator. Hosts in Mininet
share the kernel clock, so one-way latency is meaningful.

### Socket–SDN integration

The application signals its intent to the network in two ways, and the network
acts on it:

1. **UDP destination port per class** — what the controller matches on to pick
   a queue.
2. **DSCP marking** (`IP_TOS` socket option) — visible in `tcpdump`, and the
   fallback classification key.

In the other direction the network changes what the application experiences:
when the controller flips the policy mid-run, the per-second timeline in the
receiver's JSON shows voice and video recovering within a couple of seconds.

## 3. Dynamic SDN behaviour

The controller polls `OFPPortStatsRequest` on the bottleneck port every 2 s and
computes utilisation.

* utilisation ≥ **80 %** → `congestion_detected` → policy switches to **qos**
  (each class into its own queue).
* utilisation ≤ **60 %** → `congestion_cleared` → policy switches back to
  **flat**.

The two thresholds give hysteresis so the policy cannot oscillate. Every
decision is logged to `results/controller.jsonl` and replayed in the report.

Policies can also be driven by hand while traffic is running:

```bash
python3 qosctl.py status
python3 qosctl.py policy flat      # static baseline
python3 qosctl.py policy qos       # prioritisation pinned on
python3 qosctl.py policy auto      # congestion-driven
python3 qosctl.py set video 1      # remap a class to another queue, live
python3 qosctl.py events
```

## 4. Install and run (Ubuntu)

```bash
./setup.sh              # mininet, openvswitch, ryu or os-ken, matplotlib
python3 test_qos.py     # self-check, no root needed
sudo ./run.sh           # all three scenarios + report
```

`run.sh` starts the controller, runs the scenarios, and writes
`results/report.md`, `results/throughput.png` and one JSON per run.

Run pieces separately if you prefer:

```bash
# terminal 1
QOS_MODE=auto QOS_EVENT_LOG=$PWD/results/controller.jsonl ryu-manager qos_controller.py
# terminal 2
sudo python3 topology.py --scenario dynamic --duration 30
sudo python3 topology.py --cli          # interactive Mininet
python3 analyze.py
```

### Scenarios

| Scenario | Controller policy | What it shows |
|---|---|---|
| `baseline` | pinned **flat** | Static network: one shared best-effort queue, bulk starves voice and video |
| `dynamic` | **auto** | Starts flat; bulk joins at t = 10 s, the controller detects congestion and switches to qos mid-run |
| `qos` | pinned **qos** | Prioritisation on for the whole run — the upper bound |

In each run voice and video start at t = 0 and the bulk class joins at
t = 10 s, which is the congestion event.

## 5. Test cases

| # | Test | How | Expected |
|---|---|---|---|
| T1 | Protocol + measurement correctness | `python3 test_qos.py` | All checks pass; injected loss and a malformed packet are both counted |
| T2 | Connectivity and flow installation | `sudo python3 topology.py --cli` then `pingall` | 0 % dropped; `ovs-ofctl -O OpenFlow13 dump-flows s1` shows 3 classification flows in table 0 |
| T3 | Queues exist | `ovs-vsctl list qos; ovs-vsctl list queue` | One linux-htb QoS with max-rate 10 Mbps and queues 0, 1, 2 |
| T4 | Uncongested behaviour | run with `--bulk-delay 999` | All classes get their full offered rate, near-zero loss |
| T5 | Congestion, no QoS (baseline) | `--scenario baseline` | Link saturates, voice and video lose throughput and gain latency |
| T6 | Congestion, with QoS | `--scenario qos` | Voice ≈ full rate with low loss and latency; bulk absorbs the shortfall |
| T7 | Dynamic reaction | `--scenario dynamic` | `congestion_detected` and `policy_change` in `results/controller.jsonl`; receiver timeline shows recovery within ~2 polls |
| T8 | Live policy change | during a run: `python3 qosctl.py policy flat` | Timeline shows voice degrading again immediately |
| T9 | Live queue remap | `python3 qosctl.py set bulk 1` | Bulk is promoted; voice degrades — proves the mapping is enforced by the controller, not the app |
| T10 | Robustness: controller restart | kill and restart the controller mid-run | Senders keep running; flows are reinstalled on reconnect; traffic resumes |

## 6. Metrics and comparison

`analyze.py` reports, per class and per scenario:

* **throughput** (Mbps, overall and per second)
* **packet loss** (from the sequence-number gap)
* **latency** — average, min, max, standard deviation
* **jitter** — RFC 3550 smoothed interarrival jitter
* **Jain's fairness index**, both raw and weighted by each class's offered
  demand. Raw fairness alone is misleading here: a QoS network is *supposed*
  to be unequal. The demand-weighted index measures what we actually want —
  how close each class is to the service it asked for — and the QoS runs score
  higher on it than the baseline even though their raw index is lower.

The baseline run is the comparison point: `analyze.py` prints
`baseline -> dynamic` and `baseline -> qos` deltas for throughput, loss and
latency.

## 7. Configuration

Controller behaviour is set by environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `QOS_MODE` | `auto` | `auto`, `qos` or `flat` at launch |
| `QOS_BOTTLENECK_DPID` | `1` | Switch holding the monitored link |
| `QOS_BOTTLENECK_PORT` | `4` | Port number of the bottleneck (`s1-eth4`) |
| `QOS_POLL_INTERVAL` | `2.0` | Port-stats poll period, seconds |
| `QOS_HIGH_WATER` | `0.80` | Utilisation that triggers prioritisation |
| `QOS_LOW_WATER` | `0.60` | Utilisation that releases it |
| `QOS_EVENT_LOG` | unset | Append controller decisions as JSON lines |

Traffic classes, rates, ports, DSCP values, queue ids and the bottleneck
capacity all live in `qos_proto.py` — change them in one place and the
application, the controller and the queue setup all follow.

## 8. Troubleshooting

* **`no ryu-manager/osken-manager found`** — run `./setup.sh`. On Python ≥ 3.10
  Ryu fails to import; `os-ken` is the maintained fork and the controller picks
  up whichever is installed.
* **Switches never connect** — check `results/controller.log` and that nothing
  else holds TCP 6653.
* **Queues missing after a crash** — `sudo ovs-vsctl --all destroy qos;
  sudo ovs-vsctl --all destroy queue; sudo mn -c`.
* **Throughput below the cap for every class** — the Python senders are pacing
  in userspace; lower `rate_bps` for `bulk` or raise the packet size if the VM
  is CPU-bound.
