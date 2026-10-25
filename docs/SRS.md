# Software Requirements Specification

## QoS Traffic Management System (UDP Sockets + Software-Defined Networking)

| | |
|---|---|
| Document | Software Requirements Specification (SRS) |
| Project | QoS Traffic Management System — SDN/Socket Programming Mini Project |
| Standard | IEEE Std 830-1998 / ISO/IEC/IEEE 29148 |
| Version | 1.0 |
| Status | Baseline |
| Repository | https://github.com/Yug-Gajera/qos-traffic-management-sdn |

---

## Table of Contents

1. [Introduction](#1-introduction)
2. [Overall Description](#2-overall-description)
3. [Specific Requirements](#3-specific-requirements)
4. [Verification and Traceability](#4-verification-and-traceability)
5. [Appendices](#5-appendices)

---

## 1. Introduction

### 1.1 Purpose

This document specifies the requirements for a QoS Traffic Management System: a
UDP socket application that generates multiple classes of network traffic, and a
Software-Defined Networking (SDN) controller that classifies those flows and
dynamically prioritises them when the network becomes congested.

It is written for the project evaluators, and for any developer who must
reproduce, extend or test the system. It states **what** the system must do and
the constraints it operates under. Implementation choices (queue disciplines,
data structures, library selection) are design decisions and are recorded in the
project README, not here, except where a specific technology is itself a
constraint imposed on the project.

### 1.2 Scope

The system, referred to throughout as **QTMS**, consists of:

* a **UDP traffic generator** that produces three application traffic classes
  with distinct rates, packet sizes and service expectations;
* a **concurrent UDP sink** that receives all classes simultaneously and
  measures delivered quality of service;
* an **SDN controller** that installs OpenFlow rules classifying each flow and
  steering it into a corresponding egress queue, and that changes this policy at
  runtime in response to measured network conditions;
* an **emulated network** (Mininet + Open vSwitch) containing a deliberate
  bandwidth bottleneck at which contention between the classes occurs;
* an **evaluation harness** that runs the system under several policies and
  produces comparative performance results.

**In scope:** connectionless (UDP) data transport, application-level protocol
design, flow classification, queue-based prioritisation, congestion detection,
runtime policy modification, and performance measurement.

**Out of scope:** reliable delivery or retransmission, encryption and
authentication of user traffic, inter-domain QoS signalling (RSVP, MPLS-TE),
physical hardware deployment, and graphical user interfaces.

### 1.3 Definitions, Acronyms and Abbreviations

| Term | Definition |
|---|---|
| **AF41** | Assured Forwarding class 4, low drop precedence; DSCP 34 |
| **BE** | Best Effort; DSCP 0; the default, unprioritised service |
| **DSCP** | Differentiated Services Code Point; 6-bit field in the IP header used to mark a packet's requested service class |
| **EF** | Expedited Forwarding; DSCP 46; the low-loss, low-latency class |
| **Flow** | A unidirectional stream of packets sharing a classification key; here, a UDP destination port |
| **HTB** | Hierarchical Token Bucket; the Linux queueing discipline used to enforce the bottleneck and its queues |
| **Jitter** | Variation in packet interarrival delay; measured per RFC 3550 |
| **Mininet** | Network emulator creating virtual hosts, switches and links on one machine |
| **OpenFlow** | Protocol by which an SDN controller programs forwarding rules into a switch |
| **OVS** | Open vSwitch; the software switch used in the emulated network |
| **QTMS** | QoS Traffic Management System; the system specified by this document |
| **Policy** | The current mapping from traffic class to egress queue. One of `flat`, `qos` |
| **Mode** | How the policy is selected. One of `flat`, `qos` (pinned) or `auto` (congestion-driven) |
| **SDN** | Software-Defined Networking; separation of the control plane from the data plane |
| **Traffic class** | A named category of application traffic (voice, video, bulk) with its own rate, marking and service expectation |
| **Utilisation** | Measured transmit rate of the bottleneck port divided by its configured capacity |

### 1.4 References

1. IEEE Std 830-1998, *Recommended Practice for Software Requirements Specifications*.
2. RFC 768, *User Datagram Protocol*.
3. RFC 2474, *Definition of the Differentiated Services Field (DS Field)*.
4. RFC 3550, *RTP: A Transport Protocol for Real-Time Applications* — jitter estimator.
5. Open Networking Foundation, *OpenFlow Switch Specification v1.3.5*.
6. R. Jain et al., *A Quantitative Measure of Fairness and Discrimination*, DEC-TR-301, 1984.
7. Project guidelines: *SDN-Socket Programming — Jackfruit Mini Project*.

### 1.5 Overview

Section 2 describes the system in context, its users, constraints and
assumptions. Section 3 states the numbered requirements. Section 4 maps each
requirement to the test that verifies it and the source file that implements it.

---

## 2. Overall Description

### 2.1 Product Perspective

QTMS is a self-contained system that runs entirely on a single Ubuntu host. It
replaces no existing system and has no external dependencies beyond the emulation
environment. Its internal structure separates cleanly into three planes:

```
  APPLICATION PLANE          CONTROL PLANE              DATA PLANE
  ----------------           -------------              ----------
  udp_sender.py  ---------.                        .--- Open vSwitch s1
  (3 instances)            \                      /     (HTB, 3 queues)
                            \                    /
  udp_receiver.py            >  qos_controller.py <---- Open vSwitch s2
  (concurrent sink)         /    (OpenFlow 1.3)    \
                           /       ^                 `-- Mininet hosts
  qosctl.py  -------------'        |
  (TCP control client)     TCP 6900 |  OpenFlow TCP 6653
```

The application plane signals its service requirements by its choice of UDP
destination port and DSCP marking. The control plane reads those signals,
programs the data plane accordingly, and observes the result through OpenFlow
port statistics — closing the loop.

### 2.2 Product Functions

| | Function |
|---|---|
| F1 | Generate multiple, independently configurable classes of UDP traffic |
| F2 | Classify application flows in the network by their application-level identity |
| F3 | Enforce per-class bandwidth guarantees and scheduling priority |
| F4 | Create reproducible, controlled congestion at a known bottleneck |
| F5 | Detect congestion from network telemetry and react without operator input |
| F6 | Permit an operator to modify traffic policy while traffic is flowing |
| F7 | Measure throughput, latency, jitter and packet loss per class |
| F8 | Quantify fairness between traffic classes and compare against a static baseline |

### 2.3 User Characteristics

| User class | Description | Assumed competence |
|---|---|---|
| **Evaluator** | Runs the demonstration and inspects the results | Familiar with Linux terminals; not required to read the source |
| **Developer** | Modifies traffic classes, thresholds or topology | Python 3, socket programming, basic OpenFlow concepts |
| **Network operator** | Changes policy at runtime during a demonstration | Able to run a CLI command; no programming required |

### 2.4 Constraints

| ID | Constraint |
|---|---|
| C-1 | The application transport **shall be UDP**, as specified by the assigned project; TCP may be used only for control and management traffic. |
| C-2 | The network **shall be emulated in Mininet**; no physical switches are used. |
| C-3 | Switch programming **shall use OpenFlow 1.3**, the lowest version providing multiple flow tables and `set_queue`. |
| C-4 | The system **shall run on Ubuntu** with Open vSwitch and a Ryu-compatible controller framework. |
| C-5 | Hosts in the emulated network **share one physical machine and one kernel clock**; one-way delay measurement depends on this and is not valid across machines. |
| C-6 | Traffic generation is performed in userspace Python and is therefore **CPU-bound at high packet rates**; the offered load must remain within what the host can sustain. |
| C-7 | The implementation **shall use only the Python standard library** for the socket application, so that no dependency is required to run or grade the core application. |

### 2.5 Assumptions and Dependencies

| ID | Assumption |
|---|---|
| A-1 | Open vSwitch is installed and its `linux-htb` QoS type is available. |
| A-2 | Either `ryu` or its maintained fork `os-ken` is installed; the controller selects whichever is present. |
| A-3 | The operator has root privileges, required by Mininet and `ovs-vsctl`. |
| A-4 | The bottleneck interface `s1-eth4` corresponds to OpenFlow port number 4; if the port numbering differs, it is supplied via configuration. |
| A-5 | No other process occupies TCP ports 6653 or 6900 on the host. |

---

## 3. Specific Requirements

Requirements use **shall** for mandatory behaviour. Each is uniquely identified
and independently verifiable.

### 3.1 External Interface Requirements

#### 3.1.1 Communications Interfaces

| ID | Requirement |
|---|---|
| **IR-1** | The system shall carry all application data over UDP (RFC 768) using Berkeley sockets of type `SOCK_DGRAM`. |
| **IR-2** | Each traffic class shall be addressed to a distinct, fixed UDP destination port: voice 5001, video 5002, bulk 5003. |
| **IR-3** | The controller shall accept OpenFlow 1.3 connections from switches on TCP port 6653. |
| **IR-4** | The controller shall expose a line-oriented TCP control interface on 127.0.0.1 port 6900, accepting one textual command per line and returning one JSON object per line. |
| **IR-5** | The sender shall set the IP TOS byte of every datagram so that its upper six bits carry the class's DSCP value (voice EF/46, video AF41/34, bulk BE/0). |

#### 3.1.2 Application Protocol Interface

| ID | Requirement |
|---|---|
| **IR-6** | Every datagram shall begin with a 15-byte fixed header in network byte order: class identifier (unsigned 8-bit), sequence number (unsigned 32-bit), transmission timestamp (IEEE 754 double, seconds since the Unix epoch), declared payload length (unsigned 16-bit). |
| **IR-7** | The header shall be followed by exactly the declared number of padding bytes, so that total datagram size equals the class's configured packet size. |
| **IR-8** | Sequence numbers shall begin at zero for each sender instance and increment by one per transmitted datagram, without reuse within a run. |

#### 3.1.3 Software Interfaces

| ID | Requirement |
|---|---|
| **IR-9** | The system shall program Open vSwitch queue configuration through the `ovs-vsctl` management interface. |
| **IR-10** | The controller shall be loadable by `ryu-manager` or `osken-manager` without source modification. |
| **IR-11** | Measurement output shall be written as JSON, with one file per measured run. |

### 3.2 Functional Requirements

#### 3.2.1 Traffic Generation

| ID | Requirement |
|---|---|
| **FR-1** | The system shall generate three concurrent classes of UDP traffic — voice, video and bulk — each from a separate host. |
| **FR-2** | Each class shall transmit at its configured nominal rate: voice 0.512 Mbps in 200-byte datagrams, video 3.0 Mbps in 1200-byte datagrams, bulk 20.0 Mbps in 1400-byte datagrams. |
| **FR-3** | The generator shall pace transmission to the configured rate, and shall not emit a compensating burst after falling more than one second behind schedule. |
| **FR-4** | The generator shall continue operating when the operating system refuses a datagram (e.g. `ENOBUFS` under congestion), recording the failure rather than terminating. |
| **FR-5** | The generator shall support a configurable start delay, so that a class may join an already-running experiment and thereby induce congestion at a known instant. |
| **FR-6** | On completion the generator shall report the number of datagrams sent, the number of send failures, and the achieved offered rate. |

#### 3.2.2 Traffic Reception and Measurement

| ID | Requirement |
|---|---|
| **FR-7** | The receiver shall service all three traffic classes concurrently, using one socket and one thread of execution per class, so that congestion in one class cannot block reception of another. |
| **FR-8** | The receiver shall validate every datagram and reject, counting separately as malformed, any datagram that is shorter than the header, declares an unknown class identifier, or whose length contradicts its declared payload length. |
| **FR-9** | The receiver shall derive packet loss per class from gaps in the received sequence numbers. |
| **FR-10** | The receiver shall compute per-class one-way latency as the difference between reception time and the transmission timestamp carried in the datagram, reporting mean, minimum, maximum and standard deviation. |
| **FR-11** | The receiver shall compute per-class interarrival jitter using the smoothed estimator of RFC 3550. |
| **FR-12** | The receiver shall record per-class throughput both as a run total and in one-second intervals, so that the effect of a mid-run policy change is observable in time. |
| **FR-13** | The receiver shall terminate after a configured duration and write all measurements to a JSON file. |

#### 3.2.3 Network Classification and Forwarding

| ID | Requirement |
|---|---|
| **FR-14** | The controller shall install, on every connected switch, an OpenFlow pipeline separating flow classification from forwarding, so that a change of policy does not disturb learned forwarding state. |
| **FR-15** | The controller shall install one classification rule per traffic class, matching IPv4, UDP and the class's destination port. |
| **FR-16** | Each classification rule shall apply the egress queue assigned to its class by the active policy, and shall then pass the packet to the forwarding stage. |
| **FR-17** | The forwarding stage shall learn source MAC-to-port bindings from received packets, forward to a learned port where one exists, and flood otherwise. |
| **FR-18** | Learned forwarding rules shall expire after 30 seconds of inactivity. |
| **FR-19** | Packets not matching any classification rule shall be forwarded normally, without queue assignment. |
| **FR-20** | The controller shall install its pipeline on any switch that connects, including one reconnecting after a controller restart. |

#### 3.2.4 Quality of Service Enforcement

| ID | Requirement |
|---|---|
| **FR-21** | The bottleneck link shall be limited to 10 Mbps by a hierarchical token bucket discipline on the egress port. |
| **FR-22** | The bottleneck shall provide three queues, each with a guaranteed minimum rate and a scheduling priority: queue 1 (voice) 2 Mbps at priority 0, queue 2 (video) 5 Mbps at priority 1, queue 0 (bulk) 0.5 Mbps at priority 2. |
| **FR-23** | The sum of the guaranteed minimum rates shall not exceed the bottleneck capacity. |
| **FR-24** | Under the `flat` policy all classes shall be assigned to the best-effort queue (queue 0), reproducing the behaviour of a network without QoS. |
| **FR-25** | Under the `qos` policy each class shall be assigned to its own queue as defined in FR-22. |

#### 3.2.5 Dynamic Control

| ID | Requirement |
|---|---|
| **FR-26** | The controller shall request port statistics from every connected switch at a configurable interval, by default every 2 seconds. |
| **FR-27** | The controller shall compute bottleneck utilisation as the transmitted byte rate of the monitored port divided by the configured bottleneck capacity. |
| **FR-28** | In `auto` mode, the controller shall switch from the `flat` policy to the `qos` policy when utilisation reaches or exceeds 80 % of capacity. |
| **FR-29** | In `auto` mode, the controller shall revert to the `flat` policy when utilisation falls to or below 60 % of capacity. |
| **FR-30** | The two thresholds of FR-28 and FR-29 shall differ, providing hysteresis such that the policy cannot oscillate while utilisation is steady. |
| **FR-31** | The controller shall record every congestion detection, congestion clearance, policy change and queue remapping as a timestamped event, both in its log and, when configured, in a machine-readable event file. |
| **FR-32** | An operator shall be able to query the controller's current mode, policy, queue mapping, congestion state, measured utilisation and connected switches. |
| **FR-33** | An operator shall be able to pin the policy to `flat` or `qos`, or return it to `auto`, while traffic is flowing; the change shall take effect without restarting the controller, the switches or the application. |
| **FR-34** | An operator shall be able to reassign any traffic class to any queue while traffic is flowing. |
| **FR-35** | Returning the controller to `auto` mode shall reset the policy to `flat` and clear the congestion state, so that an automatic run always begins from a known baseline. |
| **FR-36** | The controller shall reject malformed or unknown control commands with a structured error, and shall continue operating. |

#### 3.2.6 Experimentation and Evaluation

| ID | Requirement |
|---|---|
| **FR-37** | The system shall construct an emulated topology of three sender hosts and one receiver host, connected by two switches whose interconnecting link is the bottleneck. |
| **FR-38** | The system shall support at minimum three measured scenarios: `baseline` (policy pinned to `flat`), `qos` (policy pinned to `qos`), and `dynamic` (policy selected automatically). |
| **FR-39** | Each scenario shall apply an identical traffic profile, so that scenarios differ only in network policy and are therefore comparable. |
| **FR-40** | The evaluation harness shall report, for each class and scenario, throughput, packet loss, latency statistics and jitter. |
| **FR-41** | The evaluation harness shall compute Jain's fairness index over the classes, both unweighted and weighted by each class's offered demand. |
| **FR-42** | The evaluation harness shall present each non-baseline scenario as a delta against the baseline for throughput, loss and latency. |
| **FR-43** | The evaluation harness shall emit a written report; graphical output shall be produced when a plotting library is available and shall be omitted, without error, when it is not. |
| **FR-44** | Queue configuration shall be removed from the switches when an experiment ends, including when it ends abnormally. |

### 3.3 Non-Functional Requirements

#### 3.3.1 Performance

Targets below apply at the bottleneck under the stated offered load of
23.512 Mbps into 10 Mbps capacity.

| ID | Requirement |
|---|---|
| **NFR-1** | Under the `qos` policy during congestion, voice-class packet loss shall not exceed 2 %. |
| **NFR-2** | Under the `qos` policy during congestion, mean voice-class one-way latency shall not exceed 50 ms. |
| **NFR-3** | Under the `qos` policy during congestion, each of the voice and video classes shall achieve at least 90 % of its offered rate. |
| **NFR-4** | Under the `qos` policy, the demand-weighted Jain fairness index shall exceed that measured under the `baseline` policy for the same traffic profile. |
| **NFR-5** | In `auto` mode, the interval between the onset of congestion and the resulting policy change shall not exceed twice the statistics polling interval. |
| **NFR-6** | A policy change requested over the control interface shall be reflected in the switch within one second. |
| **NFR-7** | Measurement shall not distort the result: the receiver shall sustain the full offered load without its own processing becoming the limiting factor. |

#### 3.3.2 Reliability and Robustness

| ID | Requirement |
|---|---|
| **NFR-8** | No single malformed or hostile datagram shall terminate the receiver; such datagrams shall be counted and discarded. |
| **NFR-9** | Loss of the controller shall not terminate the socket application; senders and receiver shall continue for the remainder of the run. |
| **NFR-10** | On reconnection after a controller restart, switches shall be reprogrammed with the full pipeline without operator action. |
| **NFR-11** | A failure to write the event log shall be reported as a warning and shall not interrupt control-plane operation. |
| **NFR-12** | Failure of the control interface shall not prevent an experiment from running; the controller shall retain the policy with which it was launched. |

#### 3.3.3 Portability and Maintainability

| ID | Requirement |
|---|---|
| **NFR-13** | The socket application shall depend only on the Python 3 standard library. |
| **NFR-14** | The controller shall operate with either the `ryu` framework or the `os-ken` fork, selected at import time without source modification. |
| **NFR-15** | Traffic class definitions — identifier, port, DSCP, queue, rate, packet size, guarantee and priority — shall reside in exactly one module, imported by the generator, the receiver, the controller and the queue configuration, so that the application's and the network's view of a class cannot diverge. |
| **NFR-16** | Operational thresholds and topology-dependent values shall be overridable by environment variable without editing source. |

#### 3.3.4 Security

| ID | Requirement |
|---|---|
| **NFR-17** | The control interface shall bind to the loopback address only, and shall not be reachable from the emulated data network. |
| **NFR-18** | The system shall not transmit credentials and shall not require any. |
| **NFR-19** | The receiver shall treat all received datagrams as untrusted input and shall validate length and class identifier before interpreting any field. |

#### 3.3.5 Usability and Documentation

| ID | Requirement |
|---|---|
| **NFR-20** | A complete experiment, from a clean system to a written report, shall be executable by a single command. |
| **NFR-21** | Every component shall be independently runnable, with `--help` describing its options. |
| **NFR-22** | Documentation shall be sufficient for an independent party to reproduce the reported results from the repository alone. |
| **NFR-23** | The system shall ship an automated self-check that verifies protocol and measurement correctness without requiring root privileges, Mininet or a controller. |

---

## 4. Verification and Traceability

### 4.1 Verification Methods

| Method | Meaning |
|---|---|
| **T** | Test — automated self-check |
| **D** | Demonstration — observed during a live run |
| **A** | Analysis — derived from recorded measurements |
| **I** | Inspection — verified by examining source, configuration or switch state |

### 4.2 Traceability Matrix

Test case identifiers (T1–T10) refer to the test table in the project README.

| Requirement | Implemented in | Method | Verified by |
|---|---|---|---|
| IR-1, IR-2 | `qos_proto.py`, `udp_sender.py`, `udp_receiver.py` | T, I | T1, T2 |
| IR-3, IR-10 | `qos_controller.py` | D | T2 |
| IR-4, IR-11 | `qos_controller.py`, `qosctl.py`, `udp_receiver.py` | D | T8, T9 |
| IR-5 | `udp_sender.py` | I | `tcpdump` inspection during T2 |
| IR-6, IR-7, IR-8 | `qos_proto.py` | T | T1 |
| IR-9 | `topology.py` | I | T3 |
| FR-1, FR-2, FR-3, FR-6 | `udp_sender.py` | D, A | T4, T5 |
| FR-4 | `udp_sender.py` | D | T5 |
| FR-5 | `udp_sender.py`, `topology.py` | D | T7 |
| FR-7 | `udp_receiver.py` | T | T1 |
| FR-8 | `udp_receiver.py` | T | T1 |
| FR-9, FR-10, FR-11, FR-12 | `udp_receiver.py` | T, A | T1, T5, T6 |
| FR-13 | `udp_receiver.py` | D | T4 |
| FR-14, FR-15, FR-16 | `qos_controller.py` | I | T2 |
| FR-17, FR-18, FR-19 | `qos_controller.py` | D | T2 |
| FR-20 | `qos_controller.py` | D | T10 |
| FR-21, FR-22, FR-23 | `qos_proto.py`, `topology.py`, `test_qos.py` | T, I | T1, T3 |
| FR-24, FR-25 | `qos_controller.py` | A | T5, T6 |
| FR-26, FR-27 | `qos_controller.py` | D | T7 |
| FR-28, FR-29, FR-30 | `qos_controller.py` | D, A | T7 |
| FR-31 | `qos_controller.py`, `analyze.py` | I | T7 |
| FR-32, FR-33 | `qos_controller.py`, `qosctl.py` | D | T8 |
| FR-34 | `qos_controller.py`, `qosctl.py` | D | T9 |
| FR-35 | `qos_controller.py` | D | T7 |
| FR-36 | `qos_controller.py` | D | T8 |
| FR-37 | `topology.py` | D | T2 |
| FR-38, FR-39 | `topology.py` | D | T5, T6, T7 |
| FR-40, FR-41, FR-42, FR-43 | `analyze.py` | T, A | T1, T5–T7 |
| FR-44 | `topology.py` | I | T3 |
| NFR-1, NFR-2, NFR-3 | whole system | A | T6 |
| NFR-4 | `analyze.py` | A | T5 vs T6 |
| NFR-5 | `qos_controller.py` | A | T7 |
| NFR-6 | `qos_controller.py` | D | T8 |
| NFR-7 | `udp_receiver.py` | A | T4 |
| NFR-8, NFR-19 | `udp_receiver.py` | T | T1 |
| NFR-9, NFR-10, NFR-12 | `qos_controller.py`, senders | D | T10 |
| NFR-11 | `qos_controller.py` | I | inspection |
| NFR-13, NFR-14 | all | I | inspection |
| NFR-15 | `qos_proto.py` | T, I | T1 |
| NFR-16 | `qos_controller.py` | I | inspection |
| NFR-17 | `qos_controller.py` | I | inspection |
| NFR-18 | all | I | inspection |
| NFR-20, NFR-21, NFR-22 | `run.sh`, `README.md` | D | T2 |
| NFR-23 | `test_qos.py` | T | T1 |

### 4.3 Requirements Not Yet Verified by Measurement

The performance requirements NFR-1 through NFR-7 state **targets**. They are
verified by executing the scenarios and reading the generated report; until a
measured run is recorded in the repository they remain specified but
unconfirmed.

---

## 5. Appendices

### Appendix A — Traffic Class Specification

| Property | voice | video | bulk |
|---|---|---|---|
| Class identifier | 1 | 2 | 3 |
| UDP destination port | 5001 | 5002 | 5003 |
| DSCP (name / value) | EF / 46 | AF41 / 34 | BE / 0 |
| Nominal rate | 0.512 Mbps | 3.0 Mbps | 20.0 Mbps |
| Datagram size | 200 B | 1200 B | 1400 B |
| Approximate packet rate | 320 pps | 312 pps | 1786 pps |
| Egress queue under `qos` | 1 | 2 | 0 |
| Guaranteed minimum rate | 2 Mbps | 5 Mbps | 0.5 Mbps |
| HTB priority (0 = highest) | 0 | 1 | 2 |
| Service expectation | Low loss, low latency, low jitter | Sustained throughput, bounded latency | Throughput only; yields under contention |

Total offered load 23.512 Mbps against 10 Mbps of capacity — an oversubscription
ratio of 2.35:1, which guarantees that contention occurs and that the
prioritisation policy is exercised.

### Appendix B — Datagram Format

```
 byte  0        1                     5                              13         15        N
       +--------+---------------------+------------------------------+----------+---------+
       | class  |  sequence number    |  transmission timestamp      | payload  | padding |
       | id     |                     |  seconds since Unix epoch    | length   |         |
       | uint8  |  uint32, big-endian |  IEEE 754 double             | uint16   | N-15 B  |
       +--------+---------------------+------------------------------+----------+---------+
       |<---------------------- 15-byte fixed header ---------------------------->|
```

### Appendix C — Control Interface Commands

| Command | Effect | Requirement |
|---|---|---|
| `status` | Return mode, policy, queue mapping, congestion state, utilisation, connected switches | FR-32 |
| `events` | Return the most recent control-plane events | FR-31 |
| `policy flat` | Pin the unprioritised baseline policy | FR-33 |
| `policy qos` | Pin the prioritised policy | FR-33 |
| `policy auto` | Return to congestion-driven selection, resetting to `flat` | FR-33, FR-35 |
| `set <class> <queue>` | Reassign a traffic class to a queue | FR-34 |

### Appendix D — Operating Environment

| Component | Requirement |
|---|---|
| Operating system | Ubuntu 20.04 LTS or later |
| Python | 3.8 or later |
| Network emulator | Mininet 2.3 or later |
| Switch | Open vSwitch 2.13 or later, with `linux-htb` QoS support |
| Controller framework | `ryu` 4.34 (Python < 3.10) or `os-ken` (Python ≥ 3.10) |
| Plotting (optional) | `matplotlib` |
| Privileges | root, for Mininet and `ovs-vsctl` |

### Appendix E — Change History

| Version | Date | Change |
|---|---|---|
| 1.0 | 2026-10-25 | Initial baseline, derived from the implemented system |
