# Packet Loss Detection and Recovery over UDP with SDN

A UDP application that **detects packet loss using sequence numbers** and works
with an **SDN controller (Ryu)** to **switch traffic to an alternate path** in
**Mininet** when a link gets lossy — then measures and compares packet loss
**before vs. after** recovery.

This repository is the complete project for both deliverables:

| Deliverable | What it covers |
|---|---|
| **D1** | Problem understanding & architecture, UDP socket implementation, Mininet + SDN setup, socket–SDN integration, initial testing/demo |
| **D2** | Full application functionality, dynamic SDN rerouting, a challenging/lossy scenario, performance comparison, documentation & viva |

---

## 1. What it does (the whole story in one paragraph)

`h1` sends numbered UDP packets to `h2` across a **diamond** network that has
two paths: a **PRIMARY** path (deliberately made lossy) and a clean **BACKUP**
path. The receiver uses the **sequence numbers** to spot gaps (missing
packets) and sends an **ACK** for every packet; the sender **retransmits**
anything it doesn't hear an ACK for. When the receiver sees the recent loss
cross a threshold, it asks the **SDN controller to reroute** the flow onto the
clean backup path. Packet loss then drops back to near zero, and the app prints
a **before vs. after** comparison plus the **recovery time**.

---

## 2. Files

**Code & setup (top level) — this is what you run:**

| File | Role |
|---|---|
| `setup_controller.sh` | One-time: builds the Ryu controller environment (no sudo) |
| `setup_mininet.sh` | One-time: installs Mininet + Open vSwitch (needs sudo) |
| `run_demo.py` | One-command automated end-to-end experiment |
| `udp_sender.py` | Sends numbered packets, handles ACKs + retransmission, reports delivery stats |
| `udp_receiver.py` | Detects loss via sequence numbers, ACKs packets, triggers reroute, reports before/after |
| `ryu_controller.py` | Ryu SDN app: installs path flows, exposes a REST reroute API |
| `topology.py` | Mininet diamond topology (lossy primary + clean backup) with the Mininet CLI |
| `reroute_agent.py` | Root-namespace bridge: watches the receiver's signal file, calls the Ryu REST API |
| `reroute_client.py` | Manual control to switch paths by hand (`status` / `backup` / `primary`) |

**`docs/` folder — reading & submission material (nothing to run):**

| File | Role |
|---|---|
| `docs/REPORT.docx` | **Full project report (Word) — ready to submit** |
| `docs/REPORT.md` | Same report in Markdown |
| `docs/RESULTS.md` | All measured numbers and result tables |
| `docs/ARCHITECTURE.md` | Design, diagrams, and how the pieces fit |
| `docs/VIVA.md` | Likely viva questions with answers |
| `docs/*.png`, `*.svg` | Architecture diagram and result charts |
| `docs/make_charts.py` | Regenerates the result charts |

---

## 3. Requirements / environment  (set up and tested on WSL2 Ubuntu 26.04)

**Mininet and Ryu run on Linux only** — use your **WSL2 Ubuntu**.

Important detail about this machine: the system Python is **3.14**, and Ryu is
unmaintained and only runs on **Python ≤ 3.9**. So the controller runs in its
own Python 3.9 virtualenv (`~/ryu-venv`), built automatically by
`setup_controller.sh` using `uv` — **no sudo, no touching your system Python**.
Everything else (Mininet, the UDP app) uses the system Python 3.14.

> ✅ Already verified working on this exact setup: the Ryu controller loads, the
> REST reroute API switches paths (primary ↔ backup), and the UDP
> sender/receiver protocol (sequence numbers, ACKs, retransmission, loss
> detection) runs correctly. The only part that needs your machine with `sudo`
> is installing + running Mininet itself.

### Step 1 — put the project in your WSL home (recommended, avoids /mnt/c quirks)

Clone/unzip this `packet-loss-sdn` folder, then from a **WSL Ubuntu** terminal
copy it into your home directory. If the folder is on your Windows drive, that's:

```bash
# Adjust the source path to wherever YOU unzipped/cloned it on Windows:
cp -r "/mnt/c/Users/<your-windows-username>/path/to/packet-loss-sdn" ~/packet-loss-sdn
cd ~/packet-loss-sdn
```

(Or if you cloned it directly inside WSL, just `cd` into it — no copy needed.)

### Step 2 — set up the controller  (no sudo)

```bash
bash setup_controller.sh
```

This fetches a standalone Python 3.9, creates `~/ryu-venv`, and installs a
working Ryu. (Takes a couple of minutes the first time.)

### Step 3 — set up Mininet + Open vSwitch  (needs sudo — it will prompt you)

```bash
bash setup_mininet.sh
```

This installs Mininet + OVS and starts the OVS daemons. On WSL2 the switches use
OVS's **userspace datapath** (the WSL kernel has no openvswitch module), which
is why `topology.py` / `run_demo.py` default to `--switch user`.

---

## 4. Quick start — the automated demo (recommended for the demo/viva)

All commands run from `~/packet-loss-sdn`.

**Terminal 1 — start the controller** (from the 3.9 venv):

```bash
~/ryu-venv/bin/ryu-manager ryu_controller.py
```

**Terminal 2 — run the full experiment:**

```bash
sudo python3 run_demo.py --loss 30 --count 2000 --rate 50
```

You'll see the receiver detect loss, the controller reroute to the backup path,
and finally a **SENDER REPORT** and **RECEIVER REPORT** with before/after loss
and the recovery time. Outputs are also saved to `sender_output.txt` and
`receiver_output.txt`.

Clean up a wedged run with `sudo mn -c` if needed.

---

## 4b. Enhanced Demo 2 — Multi-client / Multi-server + Statistics  ⭐

The upgraded Demo-2 version: **three clients and three servers run at the same
time**, all sharing the diamond, and a **single SDN reroute recovers every flow
at once**. It also produces full **statistics** (per-flow, aggregate, before vs
after) and charts.

Use the **multi** controller for this (`ryu_controller_multi.py`).

**Terminal 1 — multi controller:**

```bash
~/ryu-venv/bin/ryu-manager ryu_controller_multi.py
```

**Terminal 2 — the multi-flow demo:**

```bash
sudo python3 run_demo_multi.py --loss 30 --count 2000 --rate 50
```

At the end it prints **per-flow detail**, an **aggregate summary**, and a
**before-vs-after** comparison, and writes charts to `stats_multi/`
(`chart_per_flow_loss.png`, `chart_per_flow_delivery.png`,
`chart_overall_before_after.png`). Re-print stats from an existing run with
`python3 collect_stats.py --stats-dir stats_multi`. Baseline for comparison:
`sudo python3 run_demo_multi.py --loss 30 --no-reroute`.

Files: `ryu_controller_multi.py`, `run_demo_multi.py`, `topology_multi.py`,
`collect_stats.py`. Prep docs: `docs/D2_PRESENTATION.docx`,
`docs/D2_COMPLETE_GUIDE.docx`, `docs/D2_CONCEPT_TO_CODE.docx`.

---

## 5. Manual run (good for understanding / live viva control)

**Terminal 1 — controller:**

```bash
~/ryu-venv/bin/ryu-manager ryu_controller.py
```

**Terminal 2 — topology (opens the Mininet CLI):**

```bash
sudo python3 topology.py --loss 30 --switch user
```

**Terminal 3 — reroute agent (root namespace, system Python is fine):**

```bash
python3 reroute_agent.py
```

**Back in the Mininet CLI (Terminal 2):**

```bash
mininet> h2 python3 udp_receiver.py --port 5005 --loss-threshold 0.15 > recv.txt 2>&1 &
mininet> h1 python3 udp_sender.py --dst-ip 10.0.0.2 --dst-port 5005 --count 2000 --rate 50
```

Then read the reports:

```bash
mininet> h2 cat recv.txt
```

**Drive the reroute by hand instead of automatically** (Terminal 3, root ns):

```bash
python3 reroute_client.py status     # which path is active
python3 reroute_client.py backup     # force onto backup path
python3 reroute_client.py primary     # force back to primary
```

You can verify the installed flows at any time:

```bash
mininet> sh ovs-ofctl -O OpenFlow13 dump-flows s1
```

> Tip: confirm the controller is reachable before a run with
> `python3 reroute_client.py status` — it should print the active path.

---

## 6. Suggested experiments for the "Performance Evaluation & Comparison" marks

Run the demo at several loss levels and tabulate the receiver's before/after
loss and recovery time:

```bash
sudo python3 run_demo.py --loss 10
sudo python3 run_demo.py --loss 30
sudo python3 run_demo.py --loss 50
```

Good things to put in your report:

- **Before vs. after loss %** at each loss level (bar chart).
- **Recovery time** (detection → clean traffic).
- **Retransmission overhead** before reroute (from the sender report).
- **Delivery success rate** with vs. without rerouting (run once and kill the
  reroute agent to see the "no recovery" baseline).

---

## 7. Challenging scenario / robustness ideas (D2)

- Crank `--loss` to 60–70% and show the app still detects and recovers.
- Lower `--threshold` to react faster, or raise it to show the trade-off.
- Increase `--rate` to stress the reliability layer.
- Add loss to the *backup* path mid-run (`link s3 s4 ...` tc) to show the
  system staying resilient / not falsely recovering.

See `docs/ARCHITECTURE.md` for how every piece works and `docs/VIVA.md` for the viva.
