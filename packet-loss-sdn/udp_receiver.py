#!/usr/bin/env python3
"""
udp_receiver.py
---------------
The receiving side of the "Packet Loss Detection and Recovery" project.

What it does:
    1. Receives numbered UDP packets and ACKs every one of them.
    2. Uses the SEQUENCE NUMBERS to detect LOSS:
         - a "gap" (e.g. we saw 5 then 8) means 6 and 7 are currently missing.
    3. Keeps a sliding WINDOW of recent packets and computes a loss ratio.
    4. When the recent loss ratio crosses a threshold, it asks the SDN layer
       to reroute traffic onto the backup path. It does this by writing a
       small signal file that `reroute_agent.py` (running in the root
       namespace, where it can reach the Ryu REST API) picks up.

       We use a signal FILE because Mininet hosts live in isolated network
       namespaces and cannot directly reach the controller's REST port on the
       root namespace -- but they DO share the same filesystem. This keeps the
       "app detects -> SDN reacts" control loop clean and reliable.
    5. At the end it prints a BEFORE vs. AFTER comparison and the recovery time.

Run example (inside Mininet, on host h2):
    python3 udp_receiver.py --port 5005 --window 100 --loss-threshold 0.15
"""

import argparse
import json
import os
import socket
import time


# A packet counts as "lost" for reporting if it never arrives within this many
# seconds after a later packet showed up (gives late/reordered packets a chance).
LATE_GRACE_SECONDS = 2.0


class Window:
    """A small sliding window used to measure the RECENT loss ratio."""

    def __init__(self, size):
        self.size = size
        self.samples = []  # each entry: 1 if received, 0 if detected missing

    def add(self, received):
        self.samples.append(1 if received else 0)
        if len(self.samples) > self.size:
            self.samples.pop(0)

    def loss_ratio(self):
        if not self.samples:
            return 0.0
        lost = self.samples.count(0)
        return lost / len(self.samples)


def main():
    parser = argparse.ArgumentParser(description="UDP receiver with loss detection + reroute trigger.")
    parser.add_argument("--port", type=int, default=5005, help="UDP port to listen on")
    parser.add_argument("--window", type=int, default=100, help="Sliding window size (packets)")
    parser.add_argument("--loss-threshold", type=float, default=0.15,
                        help="Loss ratio (0..1) that triggers a reroute")
    parser.add_argument("--signal-file", default="/tmp/reroute.signal",
                        help="File written to ask the SDN layer to reroute")
    parser.add_argument("--run-time", type=float, default=120.0,
                        help="Safety timeout: stop after this many seconds")
    parser.add_argument("--no-reroute", action="store_true",
                        help="Baseline mode: detect loss but do NOT trigger a reroute "
                             "(shows sustained loss on the primary path for comparison)")
    parser.add_argument("--flow-name", default="flow",
                        help="A label for this client->server flow (used in the stats file)")
    parser.add_argument("--stats-file", default=None,
                        help="If given, write a JSON summary of this flow here (for collect_stats.py)")
    args = parser.parse_args()

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", args.port))
    sock.settimeout(1.0)

    # Make sure we start from a clean slate for the reroute signal.
    if os.path.exists(args.signal_file):
        os.remove(args.signal_file)

    highest_seq = -1          # highest sequence number seen so far
    received_seqs = set()     # every sequence number we have received
    missing = {}              # seq -> time first noticed missing (not yet seen)

    window = Window(args.window)

    detected = False            # have we announced loss yet (prints once)
    reroute_done = False        # have we actually switched to the backup path
    reroute_trigger_time = None
    reroute_recovered_time = None

    # Counters split into "before reroute" and "after reroute" buckets so we
    # can compare packet loss before and after recovery.
    #   path_loss   = packets missing on FIRST transmission (raw loss on the
    #                 path -- this is what the reroute fixes). This is the
    #                 headline before/after number.
    #   received    = distinct packets that arrived.
    #   unrecovered = packets still missing at the end even after retransmission
    #                 (should be ~0 -- shows the reliability layer worked).
    stats = {
        "before": {"received": 0, "path_loss": 0, "unrecovered": 0},
        "after": {"received": 0, "path_loss": 0, "unrecovered": 0},
    }

    print("=" * 60)
    print("UDP RECEIVER listening on port %d" % args.port)
    print("  window = %d packets   loss threshold = %.0f%%" %
          (args.window, args.loss_threshold * 100))
    print("=" * 60)

    sender_addr = None
    first_packet_time = None
    hard_deadline = time.time() + args.run_time

    while time.time() < hard_deadline:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            # No packet this second. Still give late packets a chance to expire.
            expire_missing(missing, window, stats, reroute_done)
            continue

        sender_addr = addr
        text = data.decode("utf-8", errors="replace")

        # The sender signals the end of the run with an END marker.
        if text.startswith("END|"):
            break

        parts = text.split("|", 2)
        if len(parts) < 3:
            continue

        try:
            seq = int(parts[0])
        except ValueError:
            continue

        now = time.time()
        if first_packet_time is None:
            first_packet_time = now

        # --- Always ACK the packet so the sender knows it arrived. ----------
        ack = ("ACK|%d" % seq).encode("utf-8")
        sock.sendto(ack, addr)

        # A retransmission of an already-seen packet: count the ACK, skip stats.
        if seq in received_seqs:
            continue

        received_seqs.add(seq)

        # If this packet had earlier been marked missing, it finally arrived.
        if seq in missing:
            del missing[seq]

        bucket = "after" if reroute_done else "before"
        stats[bucket]["received"] += 1
        window.add(received=True)

        # --- Detect NEW gaps: any sequence numbers skipped over are missing. -
        if seq > highest_seq:
            for gap_seq in range(highest_seq + 1, seq):
                if gap_seq not in received_seqs and gap_seq not in missing:
                    missing[gap_seq] = now
                    window.add(received=False)
                    # Raw path loss, counted the moment the gap appears and
                    # bucketed by whether we have rerouted yet.
                    stats[bucket]["path_loss"] += 1
            highest_seq = seq

        # Let genuinely-late packets expire into the "lost" count.
        expire_missing(missing, window, stats, reroute_done)

        # --- Decide whether to ask the SDN layer to reroute. ----------------
        ratio = window.loss_ratio()
        if (not detected) and ratio >= args.loss_threshold and len(window.samples) >= args.window // 2:
            detected = True
            if args.no_reroute:
                print(">>> LOSS DETECTED: recent loss = %.1f%% -- BASELINE mode, NOT rerouting"
                      % (ratio * 100))
            else:
                reroute_done = True
                reroute_trigger_time = now
                trigger_reroute(args.signal_file, ratio)
                print(">>> LOSS DETECTED: recent loss = %.1f%% -- requesting reroute to backup path"
                      % (ratio * 100))

        # Once rerouted, watch for recovery: a clean window means we recovered.
        if reroute_done and reroute_recovered_time is None:
            if ratio <= 0.02 and bucket == "after" and stats["after"]["received"] >= args.window // 2:
                reroute_recovered_time = now
                recovery = reroute_recovered_time - reroute_trigger_time
                print(">>> RECOVERED: loss back down to %.1f%% after reroute (%.2f s)"
                      % (ratio * 100, recovery))

    # Any still-missing packets at the end were never recovered.
    for seq, t in list(missing.items()):
        bucket = "after" if reroute_done else "before"
        stats[bucket]["unrecovered"] += 1

    sock.close()
    print_report(stats, reroute_trigger_time, reroute_recovered_time,
                 first_packet_time, reroute_done, args.no_reroute)

    if args.stats_file:
        write_stats_json(args, stats, reroute_trigger_time,
                         reroute_recovered_time, reroute_done)


def write_stats_json(args, stats, trigger_time, recovered_time, reroute_done):
    """Write a machine-readable before/after summary so collect_stats.py can
    aggregate many flows together."""
    def loss_pct(bucket):
        total = bucket["received"] + bucket["path_loss"]
        return round(100.0 * bucket["path_loss"] / total, 2) if total else 0.0

    before, after = stats["before"], stats["after"]
    recovery_time = None
    if reroute_done and trigger_time is not None and recovered_time is not None:
        recovery_time = round(recovered_time - trigger_time, 2)

    data = {
        "role": "receiver",
        "flow": args.flow_name,
        "port": args.port,
        "rerouted": reroute_done,
        "before": {
            "received": before["received"],
            "path_loss": before["path_loss"],
            "loss_pct": loss_pct(before),
        },
        "after": {
            "received": after["received"],
            "path_loss": after["path_loss"],
            "loss_pct": loss_pct(after),
        },
        "unrecovered": before["unrecovered"] + after["unrecovered"],
        "recovery_time": recovery_time,
        "improvement_pp": round(loss_pct(before) - loss_pct(after), 2),
    }
    try:
        with open(args.stats_file, "w") as f:
            json.dump(data, f)
    except OSError as e:
        print("!!! could not write stats file %s: %s" % (args.stats_file, e))


def expire_missing(missing, window, stats, reroute_done):
    """Drop packets missing past the grace period; count them as unrecovered."""
    now = time.time()
    for seq in list(missing.keys()):
        if now - missing[seq] >= LATE_GRACE_SECONDS:
            bucket = "after" if reroute_done else "before"
            stats[bucket]["unrecovered"] += 1
            del missing[seq]


def trigger_reroute(signal_file, ratio):
    """Write the signal file that the root-namespace reroute agent watches."""
    try:
        with open(signal_file, "w") as f:
            f.write("REROUTE loss=%.3f time=%f\n" % (ratio, time.time()))
    except OSError as e:
        print("!!! could not write signal file %s: %s" % (signal_file, e))


def print_report(stats, trigger_time, recovered_time, first_packet_time,
                 reroute_done, no_reroute):
    before = stats["before"]
    after = stats["after"]

    def loss_pct(bucket):
        # Raw path loss = missing-on-first-transmission / all packets sent on
        # that path (received + path_loss).
        total = bucket["received"] + bucket["path_loss"]
        if total == 0:
            return 0.0
        return 100.0 * bucket["path_loss"] / total

    print()
    print("=" * 60)
    print("RECEIVER REPORT  (packet loss: before vs. after recovery)")
    print("=" * 60)
    print("  BEFORE reroute (traffic on the lossy PRIMARY path):")
    print("    received on path : %d" % before["received"])
    print("    lost on path     : %d" % before["path_loss"])
    print("    PATH LOSS        : %.2f %%" % loss_pct(before))
    print("  AFTER reroute (traffic on the clean BACKUP path):")
    print("    received on path : %d" % after["received"])
    print("    lost on path     : %d" % after["path_loss"])
    print("    PATH LOSS        : %.2f %%" % loss_pct(after))
    print("-" * 60)

    unrec = before["unrecovered"] + after["unrecovered"]
    print("  Packets never recovered (after retransmission): %d" % unrec)

    if no_reroute:
        print("  BASELINE run: no reroute performed -- loss stays on the primary path.")
    elif reroute_done and trigger_time is not None:
        if recovered_time is not None:
            print("  Recovery time (detect -> clean): %.2f s" % (recovered_time - trigger_time))
        else:
            print("  Reroute was triggered but a fully clean window was not seen.")
        improvement = loss_pct(before) - loss_pct(after)
        print("  Path-loss improvement after reroute : %.2f percentage points" % improvement)
    else:
        print("  No reroute was triggered (loss stayed below threshold).")
    print("=" * 60)


if __name__ == "__main__":
    main()
