#!/usr/bin/env python3
"""
udp_sender.py
-------------
A reliable-ish UDP sender for the "Packet Loss Detection and Recovery" project.

Plain UDP has NO built-in reliability: packets can be silently lost and the
sender never finds out. On top of UDP we add our own tiny reliability layer:

    1. Every data packet carries a SEQUENCE NUMBER.
    2. The receiver sends back an ACK (acknowledgement) for every packet it got.
    3. If the sender does not hear an ACK for a packet within a timeout,
       it RETRANSMITS that packet (up to a maximum number of retries).

This lets us measure how many packets were lost, how many had to be resent,
and how the picture changes before vs. after the SDN controller reroutes the
traffic onto a cleaner path.

Packet format (sender -> receiver):   b"<seq>|<send_time>|<payload>"
ACK format    (receiver -> sender):   b"ACK|<seq>"

Run example (inside Mininet, on host h1):
    python3 udp_sender.py --dst-ip 10.0.0.2 --dst-port 5005 \
                          --listen-port 5006 --count 2000 --rate 50
"""

import argparse
import json
import socket
import threading
import time


# ----------------------------------------------------------------------------
# Shared state between the sending loop and the ACK-listening thread.
# A lock protects it because two threads touch it at the same time.
# ----------------------------------------------------------------------------
class SenderState:
    def __init__(self):
        self.lock = threading.Lock()

        # seq -> dict(send_time, payload, retries)  for packets not yet ACKed
        self.unacked = {}

        # Statistics we report at the end.
        self.total_original_sent = 0   # distinct packets sent the first time
        self.total_retransmits = 0     # how many resends happened
        self.total_acked = 0           # distinct packets that got an ACK

        self.running = True            # flips to False when we want to stop


def ack_listener(sock, state):
    """
    Runs in its own thread. Waits for ACK packets from the receiver and marks
    the matching sequence number as acknowledged so the main loop stops
    retransmitting it.
    """
    sock.settimeout(1.0)

    while state.running:
        try:
            data, _addr = sock.recvfrom(2048)
        except socket.timeout:
            continue
        except OSError:
            break  # socket was closed while we were waiting

        text = data.decode("utf-8", errors="replace")

        if not text.startswith("ACK|"):
            continue

        try:
            acked_seq = int(text.split("|", 1)[1])
        except (IndexError, ValueError):
            continue

        with state.lock:
            if acked_seq in state.unacked:
                del state.unacked[acked_seq]
                state.total_acked += 1


def main():
    parser = argparse.ArgumentParser(description="Reliable UDP sender with loss detection.")
    parser.add_argument("--dst-ip", required=True, help="Receiver IP address (e.g. 10.0.0.2)")
    parser.add_argument("--dst-port", type=int, default=5005, help="Receiver UDP port")
    parser.add_argument("--listen-port", type=int, default=5006, help="Local port to receive ACKs on")
    parser.add_argument("--count", type=int, default=2000, help="Number of distinct packets to send")
    parser.add_argument("--rate", type=float, default=50.0, help="Packets per second")
    parser.add_argument("--timeout", type=float, default=0.3, help="Seconds to wait before retransmitting")
    parser.add_argument("--max-retries", type=int, default=5, help="Max retransmissions per packet")
    parser.add_argument("--payload-size", type=int, default=200, help="Payload size in bytes")
    parser.add_argument("--flow-name", default="flow",
                        help="A label for this client->server flow (used in the stats file)")
    parser.add_argument("--stats-file", default=None,
                        help="If given, write a JSON summary of this flow here (for collect_stats.py)")
    args = parser.parse_args()

    # One UDP socket is used both to SEND data and to RECEIVE ACKs.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(("0.0.0.0", args.listen_port))

    state = SenderState()

    # Start the background thread that collects ACKs.
    listener = threading.Thread(target=ack_listener, args=(sock, state), daemon=True)
    listener.start()

    payload = ("X" * args.payload_size)
    interval = 1.0 / args.rate if args.rate > 0 else 0.0
    dst = (args.dst_ip, args.dst_port)

    print("=" * 60)
    print("UDP SENDER starting")
    print("  destination : %s:%d" % (args.dst_ip, args.dst_port))
    print("  packets     : %d  at  %.1f pkt/s" % (args.count, args.rate))
    print("  timeout     : %.3f s   max retries: %d" % (args.timeout, args.max_retries))
    print("=" * 60)

    start_time = time.time()

    # --- Phase 1: send every packet once, retransmitting as we go. ----------
    for seq in range(args.count):
        send_time = time.time()
        message = ("%d|%f|%s" % (seq, send_time, payload)).encode("utf-8")
        sock.sendto(message, dst)

        with state.lock:
            state.unacked[seq] = {"send_time": send_time, "payload": payload, "retries": 0}
            state.total_original_sent += 1

        # While we wait for the next send slot, resend anything that timed out.
        next_send = start_time + (seq + 1) * interval
        while True:
            retransmit_timed_out(sock, state, dst, args)
            now = time.time()
            if now >= next_send:
                break
            time.sleep(min(0.005, next_send - now))

    # --- Phase 2: keep retransmitting stragglers until they ACK or give up. --
    deadline = time.time() + (args.timeout * (args.max_retries + 2))
    while time.time() < deadline:
        with state.lock:
            remaining = len(state.unacked)
        if remaining == 0:
            break
        retransmit_timed_out(sock, state, dst, args)
        time.sleep(0.02)

    # --- Tell the receiver we are done so it can print its report. ----------
    for _ in range(5):
        sock.sendto(b"END|END|END", dst)
        time.sleep(0.05)

    elapsed = time.time() - start_time
    state.running = False
    time.sleep(0.2)
    sock.close()

    print_report(state, elapsed, args)

    if args.stats_file:
        write_stats_json(state, elapsed, args)


def write_stats_json(state, elapsed, args):
    """Write a machine-readable summary of this flow so collect_stats.py can
    aggregate many clients/servers together."""
    with state.lock:
        sent = state.total_original_sent
        retx = state.total_retransmits
        acked = state.total_acked
        never_acked = len(state.unacked)

    data = {
        "role": "sender",
        "flow": args.flow_name,
        "dst": "%s:%d" % (args.dst_ip, args.dst_port),
        "elapsed": round(elapsed, 3),
        "sent": sent,
        "acked": acked,
        "retransmits": retx,
        "never_acked": never_acked,
        "delivery_pct": round(100.0 * acked / sent, 2) if sent else 0.0,
        "overhead_pct": round(100.0 * retx / sent, 2) if sent else 0.0,
        "throughput": round(acked / elapsed, 1) if elapsed > 0 else 0.0,
    }
    try:
        with open(args.stats_file, "w") as f:
            json.dump(data, f)
    except OSError as e:
        print("!!! could not write stats file %s: %s" % (args.stats_file, e))


def retransmit_timed_out(sock, state, dst, args):
    """
    Look through the unacked packets. Any that have waited longer than the
    timeout get resent (unless they already hit the retry limit, in which case
    we give up on them and count them as truly lost).
    """
    now = time.time()
    to_resend = []
    to_drop = []

    with state.lock:
        for seq, info in state.unacked.items():
            if now - info["send_time"] < args.timeout:
                continue
            if info["retries"] >= args.max_retries:
                to_drop.append(seq)
            else:
                to_resend.append(seq)

        for seq in to_drop:
            del state.unacked[seq]

    # Do the actual socket sends outside the lock to keep the lock short.
    for seq in to_resend:
        with state.lock:
            if seq not in state.unacked:
                continue
            info = state.unacked[seq]
            info["send_time"] = now
            info["retries"] += 1
            state.total_retransmits += 1
            payload = info["payload"]
        message = ("%d|%f|%s" % (seq, now, payload)).encode("utf-8")
        sock.sendto(message, dst)


def print_report(state, elapsed, args):
    with state.lock:
        sent = state.total_original_sent
        retx = state.total_retransmits
        acked = state.total_acked
        never_acked = len(state.unacked)

    print()
    print("=" * 60)
    print("SENDER REPORT")
    print("=" * 60)
    print("  Elapsed time            : %.2f s" % elapsed)
    print("  Distinct packets sent   : %d" % sent)
    print("  Acknowledged (delivered): %d" % acked)
    print("  Retransmissions         : %d" % retx)
    print("  Never acknowledged      : %d" % never_acked)
    if sent > 0:
        print("  Delivery success rate   : %.2f %%" % (100.0 * acked / sent))
        print("  Retransmission overhead : %.2f %%" % (100.0 * retx / sent))
    if elapsed > 0:
        print("  Throughput (delivered)  : %.1f pkt/s" % (acked / elapsed))
    print("=" * 60)


if __name__ == "__main__":
    main()
