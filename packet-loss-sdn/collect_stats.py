#!/usr/bin/env python3
"""
collect_stats.py   (DELIVERABLE 2 - statistics for the multi-client demo)
-------------------------------------------------------------------------
After run_demo_multi.py has run several client<->server flows at the same
time, each flow leaves behind two little JSON files:

    <flow>.sender.json     (from udp_sender.py   --stats-file ...)
    <flow>.receiver.json   (from udp_receiver.py --stats-file ...)

This script reads them all and produces the STATISTICS the demo is graded on:

    1. PER-FLOW DETAIL    - every client->server flow's own numbers.
    2. AGGREGATE SUMMARY  - totals across all flows (sent, delivered, loss...).
    3. BEFORE vs AFTER    - overall path loss while on the lossy primary path
                            vs. after the single reroute moved everyone to the
                            clean backup path.
    4. CHARTS (PNG)       - bar charts of the above (needs matplotlib; if it is
                            not installed the script still prints the tables and
                            just skips the images).

Run it yourself on a folder of stats files:
    python3 collect_stats.py --stats-dir stats_multi
(run_demo_multi.py calls it for you at the end.)
"""

import argparse
import glob
import json
import os


def load_flows(stats_dir):
    """Read every *.sender.json / *.receiver.json and group them by flow name."""
    flows = {}
    for path in sorted(glob.glob(os.path.join(stats_dir, "*.json"))):
        try:
            with open(path) as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        name = data.get("flow", os.path.basename(path))
        role = data.get("role")
        flows.setdefault(name, {})[role] = data
    return flows


def print_per_flow(flows):
    print("=" * 72)
    print("1) PER-FLOW DETAIL  (each client -> server session)")
    print("=" * 72)
    header = ("%-8s %-14s %7s %9s %7s %8s %9s %9s"
              % ("flow", "dst", "sent", "delivd%", "retx%", "loss_b%", "loss_a%", "recov(s)"))
    print(header)
    print("-" * 72)
    for name in sorted(flows):
        s = flows[name].get("sender", {})
        r = flows[name].get("receiver", {})
        before = r.get("before", {})
        after = r.get("after", {})
        recov = r.get("recovery_time")
        recov_s = "%.2f" % recov if isinstance(recov, (int, float)) else "-"
        print("%-8s %-14s %7s %8s%% %6s%% %7s %8s %9s"
              % (name,
                 s.get("dst", "-"),
                 s.get("sent", "-"),
                 s.get("delivery_pct", "-"),
                 s.get("overhead_pct", "-"),
                 before.get("loss_pct", "-"),
                 after.get("loss_pct", "-"),
                 recov_s))
    print()


def compute_totals(flows):
    t = {
        "sent": 0, "acked": 0, "retx": 0, "never_acked": 0,
        "before_recv": 0, "before_loss": 0,
        "after_recv": 0, "after_loss": 0,
        "unrecovered": 0, "recoveries": [],
    }
    for name in flows:
        s = flows[name].get("sender", {})
        r = flows[name].get("receiver", {})
        t["sent"] += s.get("sent", 0)
        t["acked"] += s.get("acked", 0)
        t["retx"] += s.get("retransmits", 0)
        t["never_acked"] += s.get("never_acked", 0)
        b, a = r.get("before", {}), r.get("after", {})
        t["before_recv"] += b.get("received", 0)
        t["before_loss"] += b.get("path_loss", 0)
        t["after_recv"] += a.get("received", 0)
        t["after_loss"] += a.get("path_loss", 0)
        t["unrecovered"] += r.get("unrecovered", 0)
        if isinstance(r.get("recovery_time"), (int, float)):
            t["recoveries"].append(r["recovery_time"])
    return t


def pct(part, whole):
    return (100.0 * part / whole) if whole else 0.0


def print_aggregate(flows, t):
    print("=" * 72)
    print("2) AGGREGATE SUMMARY  (all %d flows combined)" % len(flows))
    print("=" * 72)
    print("  Total distinct packets sent   : %d" % t["sent"])
    print("  Total delivered (ACKed)       : %d" % t["acked"])
    print("  Total retransmissions         : %d" % t["retx"])
    print("  Total never delivered         : %d" % t["never_acked"])
    print("  Overall delivery success rate : %.2f %%" % pct(t["acked"], t["sent"]))
    print("  Overall retransmit overhead   : %.2f %%" % pct(t["retx"], t["sent"]))
    print("  Packets never recovered       : %d" % t["unrecovered"])
    if t["recoveries"]:
        print("  Reroute recovery time         : avg %.2f s,  worst %.2f s  (%d/%d flows recovered)"
              % (sum(t["recoveries"]) / len(t["recoveries"]),
                 max(t["recoveries"]), len(t["recoveries"]), len(flows)))
    print()


def print_before_after(t):
    before_total = t["before_recv"] + t["before_loss"]
    after_total = t["after_recv"] + t["after_loss"]
    loss_before = pct(t["before_loss"], before_total)
    loss_after = pct(t["after_loss"], after_total)

    print("=" * 72)
    print("3) BEFORE vs AFTER REROUTE  (overall path loss, all flows)")
    print("=" * 72)
    print("  BEFORE (lossy PRIMARY path): sent %d, lost %d  ->  PATH LOSS %.2f %%"
          % (before_total, t["before_loss"], loss_before))
    print("  AFTER  (clean  BACKUP path): sent %d, lost %d  ->  PATH LOSS %.2f %%"
          % (after_total, t["after_loss"], loss_after))
    print("  IMPROVEMENT                : %.2f percentage points"
          % (loss_before - loss_after))
    print("=" * 72)
    return loss_before, loss_after


def make_charts(flows, t, loss_before, loss_after, out_dir):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as e:
        print("\n(charts skipped -- matplotlib not available: %s)" % e)
        return

    names = sorted(flows)

    # Chart A: per-flow path loss, before vs after.
    before_vals = [flows[n].get("receiver", {}).get("before", {}).get("loss_pct", 0) for n in names]
    after_vals = [flows[n].get("receiver", {}).get("after", {}).get("loss_pct", 0) for n in names]
    x = range(len(names))
    width = 0.38
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar([i - width / 2 for i in x], before_vals, width, label="before reroute", color="#d9534f")
    ax.bar([i + width / 2 for i in x], after_vals, width, label="after reroute", color="#5cb85c")
    ax.set_xticks(list(x))
    ax.set_xticklabels(names)
    ax.set_ylabel("path loss (%)")
    ax.set_title("Per-flow packet loss: before vs after reroute")
    ax.legend()
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "chart_per_flow_loss.png"), dpi=120)
    plt.close(fig)

    # Chart B: per-flow delivery success rate.
    deliv = [flows[n].get("sender", {}).get("delivery_pct", 0) for n in names]
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.bar(names, deliv, color="#0275d8")
    ax.set_ylim(0, 105)
    ax.set_ylabel("delivery success (%)")
    ax.set_title("Per-flow delivery success rate (with recovery)")
    for i, v in enumerate(deliv):
        ax.text(i, v + 1, "%.1f%%" % v, ha="center", fontsize=9)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "chart_per_flow_delivery.png"), dpi=120)
    plt.close(fig)

    # Chart C: overall before vs after (aggregate).
    fig, ax = plt.subplots(figsize=(5, 4))
    ax.bar(["BEFORE\n(primary)", "AFTER\n(backup)"], [loss_before, loss_after],
           color=["#d9534f", "#5cb85c"])
    ax.set_ylabel("overall path loss (%)")
    ax.set_title("Overall packet loss: before vs after reroute")
    for i, v in enumerate([loss_before, loss_after]):
        ax.text(i, v + 0.3, "%.1f%%" % v, ha="center", fontsize=10)
    fig.tight_layout()
    fig.savefig(os.path.join(out_dir, "chart_overall_before_after.png"), dpi=120)
    plt.close(fig)

    print("\nCharts written to %s :" % out_dir)
    for f in ("chart_per_flow_loss.png", "chart_per_flow_delivery.png",
              "chart_overall_before_after.png"):
        print("   " + os.path.join(out_dir, f))


def main():
    parser = argparse.ArgumentParser(description="Aggregate multi-flow packet-loss statistics.")
    parser.add_argument("--stats-dir", default="stats_multi",
                        help="Folder holding the *.sender.json / *.receiver.json files")
    parser.add_argument("--no-charts", action="store_true", help="Skip PNG chart generation")
    args = parser.parse_args()

    flows = load_flows(args.stats_dir)
    if not flows:
        print("No stats files found in %s -- did the flows run?" % args.stats_dir)
        return

    print()
    print("#" * 72)
    print("#  MULTI-CLIENT / MULTI-SERVER STATISTICS  (%d concurrent flows)" % len(flows))
    print("#" * 72)
    print()

    print_per_flow(flows)
    t = compute_totals(flows)
    print_aggregate(flows, t)
    loss_before, loss_after = print_before_after(t)

    if not args.no_charts:
        make_charts(flows, t, loss_before, loss_after, args.stats_dir)


if __name__ == "__main__":
    main()
