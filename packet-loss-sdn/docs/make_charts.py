#!/usr/bin/env python3
"""
make_charts.py
--------------
Generate the result charts for the report from the measured sweep data.
Edit the arrays below if you re-run with different settings.

    python3 make_charts.py      # writes PNGs into this folder
"""

import matplotlib
matplotlib.use("Agg")  # no display needed
import matplotlib.pyplot as plt
import numpy as np

RED = "#c0392b"
GREEN = "#27ae60"
BLUE = "#2980b9"
ORANGE = "#e67e22"

# ---- Measured sweep (2000 pkts @ 50 pkt/s) ----------------------------------
# WITH SDN reroute:
loss_levels = [20, 30, 40, 60]
before_loss = [23.53, 32.00, 40.00, 48.00]   # path loss before reroute (%)
after_loss  = [0.00, 0.00, 0.00, 0.15]        # path loss after reroute (%)
recovery    = [1.80, 1.81, 1.64, 1.62]        # recovery time (s)
ovh_reroute = [1.70, 1.85, 2.75, 3.90]        # retransmission overhead (%)
del_reroute = [100.0, 100.0, 100.0, 100.0]    # delivery success (%)

# BASELINE (no reroute) -- only ran 30 and 60:
base_levels = [30, 60]
ovh_base    = [97.60, 302.55]
del_base    = [98.60, 66.35]


def annotate(ax, bars, fmt="%.1f"):
    for b in bars:
        h = b.get_height()
        ax.text(b.get_x() + b.get_width() / 2, h, fmt % h,
                ha="center", va="bottom", fontsize=9, fontweight="bold")


# Chart 1: path loss BEFORE vs AFTER reroute, across loss levels
fig, ax = plt.subplots(figsize=(6, 4))
x = np.arange(len(loss_levels))
w = 0.38
b1 = ax.bar(x - w/2, before_loss, w, label="Before reroute (primary)", color=RED, edgecolor="black", linewidth=0.5)
b2 = ax.bar(x + w/2, after_loss, w, label="After reroute (backup)", color=GREEN, edgecolor="black", linewidth=0.5)
annotate(ax, b1); annotate(ax, b2)
ax.set_xticks(x); ax.set_xticklabels(["%d%%" % l for l in loss_levels])
ax.set_xlabel("Injected loss on primary path"); ax.set_ylabel("Path loss (%)")
ax.set_title("Packet loss before vs. after SDN reroute", fontweight="bold")
ax.legend(); ax.grid(axis="y", linestyle=":", alpha=0.5); ax.set_ylim(0, 55)
fig.tight_layout(); fig.savefig("chart_loss_before_after.png", dpi=150); plt.close(fig)

# Chart 2: retransmission overhead -- reroute vs baseline (log scale, huge gap)
fig, ax = plt.subplots(figsize=(6, 4))
x = np.arange(len(base_levels))
r = [ovh_reroute[loss_levels.index(l)] for l in base_levels]  # 30,60 reroute overhead
b1 = ax.bar(x - w/2, r, w, label="With SDN reroute", color=GREEN, edgecolor="black", linewidth=0.5)
b2 = ax.bar(x + w/2, ovh_base, w, label="No reroute (baseline)", color=RED, edgecolor="black", linewidth=0.5)
annotate(ax, b1, "%.1f%%"); annotate(ax, b2, "%.1f%%")
ax.set_xticks(x); ax.set_xticklabels(["%d%% loss" % l for l in base_levels])
ax.set_ylabel("Retransmission overhead (%)")
ax.set_title("Cost of recovery: SDN reroute vs. retransmission-only", fontweight="bold")
ax.legend(); ax.grid(axis="y", linestyle=":", alpha=0.5)
fig.tight_layout(); fig.savefig("chart_overhead_comparison.png", dpi=150); plt.close(fig)

# Chart 3: delivery success -- reroute vs baseline
fig, ax = plt.subplots(figsize=(6, 4))
d = [del_reroute[loss_levels.index(l)] for l in base_levels]
b1 = ax.bar(x - w/2, d, w, label="With SDN reroute", color=GREEN, edgecolor="black", linewidth=0.5)
b2 = ax.bar(x + w/2, del_base, w, label="No reroute (baseline)", color=BLUE, edgecolor="black", linewidth=0.5)
annotate(ax, b1, "%.1f%%"); annotate(ax, b2, "%.1f%%")
ax.set_xticks(x); ax.set_xticklabels(["%d%% loss" % l for l in base_levels])
ax.set_ylabel("End-to-end delivery (%)"); ax.set_ylim(0, 110)
ax.set_title("End-to-end delivery success", fontweight="bold")
ax.legend(); ax.grid(axis="y", linestyle=":", alpha=0.5)
fig.tight_layout(); fig.savefig("chart_delivery.png", dpi=150); plt.close(fig)

# Chart 4: recovery time across loss levels (fast & stable)
fig, ax = plt.subplots(figsize=(6, 4))
bars = ax.bar(["%d%%" % l for l in loss_levels], recovery, color=ORANGE, width=0.5,
              edgecolor="black", linewidth=0.5)
annotate(ax, bars, "%.2f s")
ax.set_xlabel("Injected loss on primary path"); ax.set_ylabel("Recovery time (s)")
ax.set_title("Recovery time (detect -> clean) stays ~1.7 s", fontweight="bold")
ax.grid(axis="y", linestyle=":", alpha=0.5); ax.set_ylim(0, 2.5)
fig.tight_layout(); fig.savefig("chart_recovery_time.png", dpi=150); plt.close(fig)

print("Wrote: chart_loss_before_after.png, chart_overhead_comparison.png, "
      "chart_delivery.png, chart_recovery_time.png")
