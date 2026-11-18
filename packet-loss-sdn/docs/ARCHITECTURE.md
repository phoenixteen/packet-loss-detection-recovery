# Architecture

## 1. The problem

UDP is **connectionless and unreliable**: it does not number packets, does not
acknowledge them, and does not retransmit. If a link drops packets, the
application never finds out on its own. The goal of this project is to:

1. **Detect** packet loss at the application layer (using sequence numbers).
2. **Recover** from it in two complementary ways:
   - a small **reliability layer** on top of UDP (ACKs + retransmission), and
   - an **SDN reroute**: move the traffic onto a better physical path.
3. **Measure** loss and recovery, and compare before vs. after.

## 2. Topology

```
                 s2   (PRIMARY path: lossy, e.g. 30% on s2<->s4)
                /  \
   h1 ------- s1    s4 ------- h2
                \  /
                 s3   (BACKUP path: clean)

   Addresses:  h1 = 10.0.0.1 / 00:00:00:00:00:01
               h2 = 10.0.0.2 / 00:00:00:00:00:02

   Switch ports (fixed by link order in topology.py):
     s1: 1->h1, 2->s2, 3->s3
     s2: 1->s1, 2->s4
     s3: 1->s1, 2->s4
     s4: 1->s2, 2->s3, 3->h2
```

There are **two disjoint paths** between the hosts, which is exactly what makes
rerouting possible. The diamond also contains a **physical loop** — see §5 for
how we avoid a broadcast storm without needing a spanning tree.

## 3. The UDP reliability layer (our mini-protocol)

```
Data   (h1 -> h2):   <seq> | <send_time> | <payload>
Ack    (h2 -> h1):   ACK | <seq>
End    (h1 -> h2):   END | END | END          (sender says "I'm finished")
```

- **Sender** (`udp_sender.py`): sends each packet once with a sequence number,
  keeps a table of **unacked** packets, and a background thread collects ACKs.
  Any packet whose ACK doesn't arrive within `--timeout` is **retransmitted**
  (up to `--max-retries`). This is a simplified Automatic Repeat reQuest (ARQ).
- **Receiver** (`udp_receiver.py`): **ACKs every packet**, and uses the
  sequence numbers to find **gaps**. Seeing seq 5 then seq 8 means 6 and 7 are
  missing. A grace period lets genuinely late/reordered packets still count as
  received before they are finally declared lost.

## 4. Loss detection and the reroute decision

The receiver keeps a **sliding window** of the most recent N packets, each
marked received (1) or missing (0), and computes a **recent loss ratio**. When
that ratio crosses `--loss-threshold`, it decides the current path is bad and
requests a reroute.

### How the app reaches the SDN controller (important design point)

Mininet hosts live in **separate network namespaces**, so `h2` cannot open a
TCP connection to the Ryu REST API bound on the root namespace
(`127.0.0.1:8080`). However, all Mininet hosts **share the root filesystem**.

So the control signal travels like this:

```
  h2 (receiver)                 root namespace                  controller
  -------------                 --------------                  ----------
  detects loss                  reroute_agent.py                ryu_controller
  writes /tmp/reroute.signal --> sees the file --> POST /sdn/reroute --> switch_path("backup")
```

This cleanly separates the **application decision** ("the path is lossy, please
reroute") from the **control action** (the HTTP call to the SDN controller).
For a live demo you can also drive the reroute by hand with
`reroute_client.py`.

## 5. The SDN control logic (Ryu)

`ryu_controller.py` is **proactive and static**:

- On each switch connecting, it installs exact-match flows keyed on the
  **destination MAC address**, sending packets out the correct port for the
  **currently active path**.
- Only **s1** and **s4** actually choose between primary and backup; **s2** and
  **s3** just forward straight through.
- A **reroute** re-installs the flows on s1 and s4 so that traffic to `h2` (and
  back to `h1`) uses the s3 ports instead of the s2 ports. Re-adding a flow with
  the same match overwrites it, so the switch-over is clean.

### Why there is no broadcast storm (no spanning tree needed)

The diamond is a loop, so naive learning-switch flooding would loop forever.
We avoid this entirely:

- Hosts use **fixed MACs** and **static ARP** (set in `topology.py`), so they
  **never broadcast ARP requests**.
- The controller only ever installs **exact unicast** output flows and **never
  floods**.

With no broadcast traffic and only point-to-point unicast flows, the loop is
harmless — so we don't need STP.

### REST API

```
GET  /sdn/status    -> {"active_path": "primary" | "backup"}
POST /sdn/reroute    -> switch to backup, returns new status
POST /sdn/primary    -> switch to primary, returns new status
```

## 6. Measurement

- **Receiver report**: received/lost/loss% split into **before** and **after**
  the reroute, plus the **recovery time** (reroute trigger → first clean window)
  and the loss improvement in percentage points.
- **Sender report**: distinct packets sent, delivered (ACKed), retransmissions,
  retransmission overhead %, delivery success %, and throughput.

Together these answer every measurement the brief asks for: detect missing
packets, acknowledge + retransmit, monitor paths, trigger dynamic rerouting,
measure recovery time, and compare packet loss before and after recovery.

## 7. Data-flow summary

```
  h1: udp_sender.py  --data(seq)-->  [s1 -> s2(lossy) -> s4]  -->  h2: udp_receiver.py
          ^                                                               |
          |------------------------- ACK(seq) ----------------------------|
                                                                          |
                             loss > threshold?  -> write /tmp/reroute.signal
                                                                          |
  root ns: reroute_agent.py  --POST /sdn/reroute-->  ryu_controller.py
                                                                          |
                             controller re-installs s1/s4 flows -> BACKUP path
                                                                          |
  h1: udp_sender.py  --data(seq)-->  [s1 -> s3(clean) -> s4]  -->  h2: udp_receiver.py
                             loss drops to ~0  ->  "RECOVERED" + before/after report
```
