# Results & Performance Evaluation

All numbers below were measured on **WSL2 Ubuntu 26.04**, OVS kernel datapath,
Ryu controller, diamond topology with artificial packet loss injected on the
primary path (`s2–s4` link). Workload: **2000 UDP packets at 50 pkt/s** (h1 → h2).

Reproduce with:

```bash
# With SDN reroute (the system working), at a given loss level L:
sudo python3 run_demo.py --loss L --count 2000 --rate 50

# Baseline for comparison (loss detected but NOT rerouted):
sudo python3 run_demo.py --loss L --count 2000 --rate 50 --no-reroute
```

Charts for these numbers: `chart_loss_before_after.png`,
`chart_overhead_comparison.png`, `chart_delivery.png`, `chart_recovery_time.png`
(regenerate with `python3 make_charts.py`).

---

## 1. Headline result — packet loss before vs. after recovery (30% loss)

When loss is detected, the SDN controller reroutes traffic from the lossy
primary path to the clean backup path. Path loss collapses from 32% to 0%.

| Phase | Path | Received | Lost | **Path loss** |
|---|---|---:|---:|---:|
| **Before reroute** | primary (lossy) | 34 | 16 | **32.00 %** |
| **After reroute**  | backup (clean)  | 1966 | 0 | **0.00 %** |

**Loss improvement after reroute: 32 percentage points.**
**Recovery time (detect → clean): 1.81 s.  End-to-end delivery: 100 %.**

---

## 2. Loss-level sweep — with SDN reroute

The system was tested across a range of injected loss. In every case it detects
the loss, reroutes within ~1.7 s, and achieves **100% delivery with ~0% loss on
the new path** — and retransmission overhead stays tiny because the reroute
fixes the root cause.

| Primary-path loss | Before-reroute path loss | After-reroute path loss | Recovery time | Retransmit overhead | Delivery |
|---:|---:|---:|---:|---:|---:|
| 20 % | 23.53 % | 0.00 % | 1.80 s | 1.70 % | 100.00 % |
| 30 % | 32.00 % | 0.00 % | 1.81 s | 1.85 % | 100.00 % |
| 40 % | 40.00 % | 0.00 % | 1.64 s | 2.75 % | 100.00 % |
| 60 % | 48.00 % | 0.15 % | 1.62 s | 3.90 % | 100.00 % |

The "before" loss tracks the injected loss closely, confirming the detector
measures real path loss; "after" is ~0 on the clean backup path at every level.

---

## 3. With-reroute vs. without-reroute (the key comparison)

Same lossy link, same workload. The only difference is whether the SDN
controller is allowed to reroute. This isolates the value of SDN recovery.

### At 30% loss

| Metric | **With SDN reroute** | **Baseline (no reroute)** | Improvement |
|---|---:|---:|---|
| Path loss (steady state) | **0.00 %** | 23.02 % | loss eliminated |
| Retransmissions | **37** | 1952 | ~53× fewer |
| Retransmission overhead | **1.85 %** | 97.60 % | ~53× less waste |
| End-to-end delivery | **100.00 %** | 98.60 % | +1.4 pts |
| Throughput (delivered) | **49.7 pkt/s** | 47.0 pkt/s | +2.7 pkt/s |
| Packets never recovered | 0 | 0 | — |

### At 60% loss (stress) — the baseline collapses

| Metric | **With SDN reroute** | **Baseline (no reroute)** | Improvement |
|---|---:|---:|---|
| Retransmissions | **78** | 6051 | ~78× fewer |
| Retransmission overhead | **3.90 %** | 302.55 % | ~78× less waste |
| End-to-end delivery | **100.00 %** | 66.35 % | +33.6 pts |
| Throughput (delivered) | **49.7 pkt/s** | 31.6 pkt/s | +18.1 pkt/s |
| Packets never recovered | 0 | **89** | 89 → 0 |

### What this shows
- **Without SDN**, the retransmission layer alone is overwhelmed as loss rises:
  at 60% it resends **3× the original traffic** (302% overhead) and *still*
  loses 89 packets and only delivers 66%.
- **With SDN**, the controller moves traffic to a good path, so retransmission
  barely works (≤3.9% overhead) and nothing is lost, at every loss level.
- The two mechanisms are complementary: retransmission covers the handful of
  packets lost during the ~1.7 s detect-and-reroute window; the reroute prevents
  all further loss.

---

## 4. Robustness / challenging scenario (60% loss)

At **60%** injected loss — an extreme, highly degraded link — the system still:
- detects the loss (observed ~48% in the window),
- reroutes to the backup path in **1.62 s**,
- recovers to **0.15% path loss** and **100% end-to-end delivery**,
- with only 78 retransmissions (3.9% overhead).

The same scenario without rerouting delivers only **66.35%** and leaves **89
packets permanently lost** — demonstrating that the SDN recovery is what makes
the application robust under severe loss, not the retransmission layer alone.

---

## 5. Turning these into charts for your report (already generated)

- `chart_loss_before_after.png` — path loss before vs after reroute across 20/30/40/60%.
- `chart_overhead_comparison.png` — retransmission overhead, reroute vs baseline (30% & 60%).
- `chart_delivery.png` — end-to-end delivery, reroute vs baseline (30% & 60%).
- `chart_recovery_time.png` — recovery time stays ~1.6–1.8 s across all loss levels.

---

## 6. How the measurements are defined (for the viva)

- **Path loss** = packets missing on *first* transmission (a gap in the sequence
  numbers), as a fraction of all packets sent on that path. This is the raw loss
  the path inflicts, before the reliability layer repairs it — what the reroute fixes.
- **Retransmissions / overhead** = extra sends the ARQ layer did; overhead is
  retransmissions as a % of original packets — a direct cost measure.
- **Delivery success** = distinct packets the receiver ACKed. 100% = the
  application lost nothing end-to-end.
- **Recovery time** = from loss crossing the threshold (reroute requested) to the
  first clean measurement window on the new path.
- **Packets never recovered** = still missing at the end despite retransmission
  (0 with reroute = the system fully recovered).
