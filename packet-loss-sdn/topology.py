#!/usr/bin/env python3
r"""
topology.py
-----------
The Mininet topology for the "Packet Loss Detection and Recovery" project.

It builds a DIAMOND with two independent paths between h1 and h2:

        s2   (PRIMARY path, given artificial packet loss)
       /  \
    h1-s1   s4-h2
       \  /
        s3   (BACKUP path, clean)

Key design choices that make the SDN side simple and reliable:

  * Fixed host MACs (--mac style) so the controller can match on them.
  * STATIC ARP between h1 and h2, so hosts never broadcast ARP requests.
    Combined with the controller's exact-match flows, this means there is NO
    flooding, so the physical loop in the diamond never causes a broadcast
    storm -- we never even need a spanning tree.
  * The PRIMARY path link (s2<->s4) is given, e.g., 30% packet loss so that
    "before reroute" clearly shows loss and "after reroute" (onto the clean
    backup) clearly shows recovery.

The link order below fixes the switch port numbers that ryu_controller.py
relies on:
    s1: 1->h1, 2->s2, 3->s3
    s2: 1->s1, 2->s4
    s3: 1->s1, 2->s4
    s4: 1->s2, 2->s3, 3->h2

Run:
    sudo python3 topology.py            # default 30% loss on primary
    sudo python3 topology.py --loss 50  # heavier loss on primary
"""

import argparse

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.cli import CLI
from mininet.log import setLogLevel, info


def build(primary_loss, controller_ip, controller_port, datapath):
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink, autoSetMacs=False)

    info("*** Adding remote controller (Ryu)\n")
    net.addController(
        "c0",
        controller=RemoteController,
        ip=controller_ip,
        port=controller_port,
    )

    info("*** Adding hosts with fixed MACs\n")
    h1 = net.addHost("h1", ip="10.0.0.1/24", mac="00:00:00:00:00:01")
    h2 = net.addHost("h2", ip="10.0.0.2/24", mac="00:00:00:00:00:02")

    # datapath='user' = OVS userspace (netdev) datapath. Use it on WSL2 / any
    # kernel without the openvswitch module. 'kernel' is faster where available.
    info("*** Adding switches (datapath=%s)\n" % datapath)
    s1 = net.addSwitch("s1", protocols="OpenFlow13", datapath=datapath)
    s2 = net.addSwitch("s2", protocols="OpenFlow13", datapath=datapath)
    s3 = net.addSwitch("s3", protocols="OpenFlow13", datapath=datapath)
    s4 = net.addSwitch("s4", protocols="OpenFlow13", datapath=datapath)

    info("*** Adding links (order fixes the port numbers)\n")
    # s1 ports: 1->h1, 2->s2, 3->s3
    net.addLink(h1, s1, port1=0, port2=1)
    net.addLink(s1, s2, port1=2, port2=1)
    net.addLink(s1, s3, port1=3, port2=1)

    # PRIMARY path link gets artificial packet loss.
    net.addLink(s2, s4, port1=2, port2=1, loss=primary_loss)

    # BACKUP path link is clean.
    net.addLink(s3, s4, port1=2, port2=2)

    # s4 port 3 -> h2
    net.addLink(s4, h2, port1=3, port2=0)

    info("*** Starting network\n")
    net.build()
    net.get("c0").start()
    for sw in (s1, s2, s3, s4):
        sw.start([net.get("c0")])

    info("*** Installing static ARP so hosts never broadcast ARP\n")
    # Each host is told the other's MAC up front -> no ARP flooding needed.
    h1.setARP(ip="10.0.0.2", mac="00:00:00:00:00:02")
    h2.setARP(ip="10.0.0.1", mac="00:00:00:00:00:01")

    info("\n")
    info("*** Topology ready.\n")
    info("    PRIMARY path: s1 -> s2 -> s4   (loss = %s%% on s2-s4)\n" % primary_loss)
    info("    BACKUP  path: s1 -> s3 -> s4   (clean)\n")
    info("\n")
    info("    Try from the Mininet CLI:\n")
    info("      h2 python3 udp_receiver.py --port 5005 &\n")
    info("      h1 python3 udp_sender.py --dst-ip 10.0.0.2 --dst-port 5005 --count 2000 --rate 50\n")
    info("\n")

    CLI(net)
    net.stop()


def main():
    parser = argparse.ArgumentParser(description="Diamond topology with a lossy primary path.")
    parser.add_argument("--loss", type=float, default=30.0,
                        help="Percent packet loss on the PRIMARY path link (default 30)")
    parser.add_argument("--controller-ip", default="127.0.0.1")
    parser.add_argument("--controller-port", type=int, default=6653)  # Ryu's default
    parser.add_argument("--switch", choices=["user", "kernel"], default="kernel",
                        help="OVS datapath: 'kernel' (default), 'user' as fallback if no module")
    args = parser.parse_args()

    setLogLevel("info")
    build(args.loss, args.controller_ip, args.controller_port, args.switch)


if __name__ == "__main__":
    main()
