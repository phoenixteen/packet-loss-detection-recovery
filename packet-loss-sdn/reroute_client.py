#!/usr/bin/env python3
"""
reroute_client.py
-----------------
A tiny manual control for the SDN controller -- handy during a live demo or
viva when you want to switch paths by hand instead of waiting for automatic
loss detection.

Run in the ROOT namespace (a normal terminal):
    python3 reroute_client.py status     # which path is active?
    python3 reroute_client.py backup     # force traffic onto the backup path
    python3 reroute_client.py primary     # force traffic back to the primary path
"""

import sys
import urllib.request

CONTROLLER = "http://127.0.0.1:8080"


def call(method, path):
    url = CONTROLLER + path
    req = urllib.request.Request(url, data=b"" if method == "POST" else None, method=method)
    with urllib.request.urlopen(req, timeout=3) as resp:
        return resp.read().decode("utf-8", errors="replace")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("status", "backup", "primary"):
        print(__doc__)
        sys.exit(1)

    cmd = sys.argv[1]

    if cmd == "status":
        print(call("GET", "/sdn/status"))
    elif cmd == "backup":
        print(call("POST", "/sdn/reroute"))
    elif cmd == "primary":
        print(call("POST", "/sdn/primary"))


if __name__ == "__main__":
    main()
