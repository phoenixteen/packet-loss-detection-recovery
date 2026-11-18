#!/usr/bin/env python3
r"""
topology_multi.py   (DELIVERABLE 2 - multi-client / multi-server topology)
--------------------------------------------------------------------------
Builds the SAME diamond network used by run_demo_multi.py, but then drops you
into the Mininet CLI so you can explore it by hand. Useful for understanding the
multi-host setup or running flows manually.

        ca cb cc            sa sb sc
          \ | /               \ | /
            s1 --- s2(lossy) --- s4       PRIMARY : s1 -> s2 -> s4
              \--- s3(clean) ---/         BACKUP  : s1 -> s3 -> s4

PREREQUISITE: start the controller first, in another terminal:
    ~/ryu-venv/bin/ryu-manager ryu_controller_multi.py

Then:
    sudo python3 topology_multi.py --loss 30
In the mininet> prompt you can try, for example:
    ca ping sa
    sa python3 udp_receiver.py --port 5005 &
    ca python3 udp_sender.py --dst-ip 10.0.0.11 --dst-port 5005 --count 500
"""

import argparse

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info

# Reuse the exact host/port layout from the demo so the controller matches.
from run_demo_multi import build_net, CLIENTS, SERVERS


def main():
    parser = argparse.ArgumentParser(description="Multi-client/multi-server diamond topology (manual CLI).")
    parser.add_argument("--loss", type=float, default=30.0, help="Primary path loss %%")
    parser.add_argument("--switch", choices=["user", "kernel"], default="kernel")
    args = parser.parse_args()

    setLogLevel("info")

    info("*** Building multi-host diamond (primary loss = %s%%)\n" % args.loss)
    net, hosts = build_net(args.loss, args.switch)

    info("*** Clients: %s\n" % ", ".join("%s(%s)" % (n, ip) for n, ip, _ in CLIENTS))
    info("*** Servers: %s\n" % ", ".join("%s(%s)" % (n, ip) for n, ip, _ in SERVERS))
    info("*** Try:  ca ping sa   |   then run udp_receiver.py / udp_sender.py by hand\n")

    CLI(net)
    net.stop()


if __name__ == "__main__":
    main()
