#!/usr/bin/env python3
"""
run_demo.py
-----------
One-command automated demo for the "Packet Loss Detection and Recovery"
project. It wires everything together so you (or an evaluator) can watch the
full story unfold without typing commands into the Mininet CLI by hand:

    1. Builds the diamond topology (lossy primary path + clean backup).
    2. Starts the receiver on h2 (loss detection + reroute trigger).
    3. Starts the reroute agent in the root namespace (talks to Ryu REST).
    4. Runs the sender on h1 (numbered packets + retransmission).
    5. Collects and prints both reports (before vs. after recovery).

PREREQUISITE: start the Ryu controller FIRST, in another terminal:
    ryu-manager ryu_controller.py

Then run this (needs root for Mininet):
    sudo python3 run_demo.py --loss 30 --count 2000 --rate 50
"""

import argparse
import os
import subprocess
import time
import urllib.request

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info


HERE = os.path.dirname(os.path.abspath(__file__))
SIGNAL_FILE = "/tmp/reroute.signal"


def controller_is_up(url="http://127.0.0.1:8080/sdn/status"):
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def build_net(primary_loss, datapath):
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink)

    # Ryu listens on 6653 by default (IANA OpenFlow port), so connect there.
    c0 = net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)

    h1 = net.addHost("h1", ip="10.0.0.1/24", mac="00:00:00:00:00:01")
    h2 = net.addHost("h2", ip="10.0.0.2/24", mac="00:00:00:00:00:02")

    s1 = net.addSwitch("s1", protocols="OpenFlow13", datapath=datapath)
    s2 = net.addSwitch("s2", protocols="OpenFlow13", datapath=datapath)
    s3 = net.addSwitch("s3", protocols="OpenFlow13", datapath=datapath)
    s4 = net.addSwitch("s4", protocols="OpenFlow13", datapath=datapath)

    net.addLink(h1, s1, port1=0, port2=1)
    net.addLink(s1, s2, port1=2, port2=1)
    net.addLink(s1, s3, port1=3, port2=1)
    net.addLink(s2, s4, port1=2, port2=1, loss=primary_loss)   # lossy primary
    net.addLink(s3, s4, port1=2, port2=2)                      # clean backup
    net.addLink(s4, h2, port1=3, port2=0)

    net.build()
    c0.start()
    for sw in (s1, s2, s3, s4):
        sw.start([c0])

    h1.setARP(ip="10.0.0.2", mac="00:00:00:00:00:02")
    h2.setARP(ip="10.0.0.1", mac="00:00:00:00:00:01")

    return net, h1, h2


def main():
    parser = argparse.ArgumentParser(description="Automated packet-loss-and-recovery demo.")
    parser.add_argument("--loss", type=float, default=30.0, help="Primary path loss %%")
    parser.add_argument("--count", type=int, default=2000, help="Packets to send")
    parser.add_argument("--rate", type=float, default=50.0, help="Packets per second")
    parser.add_argument("--threshold", type=float, default=0.15, help="Loss ratio to trigger reroute")
    parser.add_argument("--switch", choices=["user", "kernel"], default="kernel",
                        help="OVS datapath: 'kernel' (default, module present on this WSL), 'user' as fallback")
    parser.add_argument("--no-reroute", action="store_true",
                        help="Baseline: detect loss but DON'T reroute (sustained loss, for comparison)")
    args = parser.parse_args()

    setLogLevel("info")

    if not controller_is_up():
        print("!!! Ryu controller is not reachable at 127.0.0.1:8080.")
        print("!!! Start it first in another terminal:  ryu-manager ryu_controller.py")
        return

    # Make sure we begin on the primary path and with no stale signal file.
    try:
        urllib.request.urlopen(
            urllib.request.Request("http://127.0.0.1:8080/sdn/primary", data=b"", method="POST"),
            timeout=3,
        )
    except Exception:
        pass
    if os.path.exists(SIGNAL_FILE):
        os.remove(SIGNAL_FILE)

    info("*** Building network (primary loss = %s%%, datapath = %s)\n" % (args.loss, args.switch))
    net, h1, h2 = build_net(args.loss, args.switch)

    # Give the switches time to connect to the controller and the controller
    # time to install the initial primary-path flows. Userspace (netdev)
    # switches on WSL2 can take a few seconds to come up.
    time.sleep(5)

    # Sanity check: can h1 reach h2 at all? If this fails, the switches did not
    # connect to the controller (no flows installed) -- surfaced early instead
    # of silently reporting 0% delivery.
    ping = h1.cmd("ping -c 3 -W 1 10.0.0.2")
    info("*** connectivity check (h1 -> h2):\n%s\n" % ping)
    if "0 received" in ping or " 100% packet loss" in ping:
        info("!!! WARNING: h1 cannot reach h2 at all. The switches likely did not\n")
        info("!!! connect to the controller. Check that ryu-manager is running.\n")

    # Dump controller-connection + flow state to a file for diagnosis.
    def sh(cmd):
        try:
            return subprocess.run(cmd, shell=True, capture_output=True,
                                  text=True, timeout=10).stdout
        except Exception as e:
            return "error: %s\n" % e
    diag = "===== ovs-vsctl show =====\n" + sh("ovs-vsctl show")
    for sw in ("s1", "s2", "s3", "s4"):
        diag += "\n===== dump-flows %s =====\n" % sw
        diag += sh("ovs-ofctl -O OpenFlow13 dump-flows %s" % sw)
    with open(os.path.join(HERE, "diag_output.txt"), "w") as f:
        f.write(diag)
    info("*** wrote diagnostics to diag_output.txt\n")

    # Name outputs by loss level + mode so a sweep of several runs does not
    # overwrite earlier results.
    mode = "baseline" if args.no_reroute else "reroute"
    suffix = "_loss%d_%s" % (int(args.loss), mode)
    recv_out = os.path.join(HERE, "receiver_output%s.txt" % suffix)
    send_out = os.path.join(HERE, "sender_output%s.txt" % suffix)

    info("*** Starting reroute agent (root namespace)\n")
    agent = subprocess.Popen(
        ["python3", os.path.join(HERE, "reroute_agent.py")],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
    )

    info("*** Starting receiver on h2\n")
    # -u = unbuffered, so its output reaches the file even though we kill it later.
    no_reroute_flag = "--no-reroute" if args.no_reroute else ""
    h2.cmd(
        "python3 -u %s --port 5005 --loss-threshold %s --run-time 120 %s "
        "> %s 2>&1 &" % (os.path.join(HERE, "udp_receiver.py"), args.threshold,
                         no_reroute_flag, recv_out)
    )
    time.sleep(1)

    info("*** Running sender on h1 (this is the experiment)\n")
    h1.cmd(
        "python3 -u %s --dst-ip 10.0.0.2 --dst-port 5005 --listen-port 5006 "
        "--count %d --rate %s > %s 2>&1"
        % (os.path.join(HERE, "udp_sender.py"), args.count, args.rate, send_out)
    )

    # Let the receiver finish writing its report.
    time.sleep(3)

    agent.terminate()
    net.stop()

    print("\n\n########################## RESULTS ##########################\n")
    for label, path in (("SENDER", send_out), ("RECEIVER", recv_out)):
        print("----- %s OUTPUT (%s) -----" % (label, path))
        try:
            with open(path) as f:
                print(f.read())
        except OSError as e:
            print("(could not read %s: %s)" % (path, e))
    print("#############################################################")


if __name__ == "__main__":
    main()
