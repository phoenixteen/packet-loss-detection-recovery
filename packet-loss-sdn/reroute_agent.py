#!/usr/bin/env python3
"""
reroute_agent.py
----------------
The bridge between the UDP app's loss detection and the SDN controller.

WHY THIS EXISTS
    The receiver (udp_receiver.py) runs on Mininet host h2, which lives in an
    isolated network namespace and therefore cannot reach the Ryu REST API on
    the root namespace (127.0.0.1:8080). But all Mininet hosts SHARE the root
    filesystem. So the receiver drops a tiny "please reroute" signal FILE, and
    this agent -- which runs in the ROOT namespace where the REST API is
    reachable -- watches that file and makes the actual HTTP call to Ryu.

    This keeps a clean separation:
        app layer     : detects loss, decides to reroute  (writes signal file)
        control layer : talks to the SDN controller        (this agent)

Run it in the ROOT namespace (a normal terminal, NOT inside `h2 ...`):
    python3 reroute_agent.py
"""

import argparse
import os
import time
import urllib.request


def post(url):
    """Send an empty POST and return the response body as text."""
    req = urllib.request.Request(url, data=b"", method="POST")
    with urllib.request.urlopen(req, timeout=3) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main():
    parser = argparse.ArgumentParser(description="Watch the signal file and reroute via Ryu REST.")
    parser.add_argument("--signal-file", default="/tmp/reroute.signal")
    parser.add_argument("--controller", default="http://127.0.0.1:8080")
    parser.add_argument("--poll", type=float, default=0.05, help="Seconds between checks")
    args = parser.parse_args()

    reroute_url = args.controller + "/sdn/reroute"

    # Start clean so an old signal file from a previous run is ignored.
    if os.path.exists(args.signal_file):
        os.remove(args.signal_file)

    print("Reroute agent watching %s" % args.signal_file)
    print("Will POST to %s when the receiver signals loss." % reroute_url)

    while True:
        if os.path.exists(args.signal_file):
            try:
                with open(args.signal_file) as f:
                    content = f.read().strip()
            except OSError:
                content = ""

            print(">>> Signal received: %s" % content)
            try:
                reply = post(reroute_url)
                print(">>> Controller rerouted to backup path. Reply: %s" % reply)
            except Exception as e:
                print("!!! Failed to call controller: %s" % e)

            # Consume the signal so we only reroute once per run.
            try:
                os.remove(args.signal_file)
            except OSError:
                pass

        time.sleep(args.poll)


if __name__ == "__main__":
    main()
