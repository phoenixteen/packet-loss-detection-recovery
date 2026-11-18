#!/usr/bin/env python3
"""
run_demo_multi.py   (DELIVERABLE 2 - multi-client / multi-server + statistics)
------------------------------------------------------------------------------
The enhanced Deliverable-2 demo asked for by the evaluator:

    * MULTI-CLIENT / MULTI-SERVER : three clients (ca, cb, cc) each talk to
      their own server (sa, sb, sc) AT THE SAME TIME, all sharing one diamond
      network with a lossy primary path and a clean backup path.

    * AUTOMATIC RECOVERY : when the shared primary path gets lossy, the receivers
      detect it and a SINGLE SDN reroute moves EVERY flow onto the clean backup
      path at once.

    * STATISTICS : each flow writes a small JSON summary; collect_stats.py then
      prints a per-flow table, an aggregate summary, a before-vs-after comparison,
      and saves bar charts (PNG).

PREREQUISITE: start the MULTI controller FIRST, in another terminal:
    ~/ryu-venv/bin/ryu-manager ryu_controller_multi.py

Then run this (needs root for Mininet):
    sudo python3 run_demo_multi.py --loss 30 --count 2000 --rate 50
"""

import argparse
import os
import shutil
import subprocess
import time
import urllib.request

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.link import TCLink
from mininet.log import setLogLevel, info


HERE = os.path.dirname(os.path.abspath(__file__))
SIGNAL_FILE = "/tmp/reroute.signal"

# Hosts. MUST match ryu_controller_multi.py.
CLIENTS = [
    # (name, ip, mac)
    ("ca", "10.0.0.1", "00:00:00:00:00:01"),
    ("cb", "10.0.0.2", "00:00:00:00:00:02"),
    ("cc", "10.0.0.3", "00:00:00:00:00:03"),
]
SERVERS = [
    ("sa", "10.0.0.11", "00:00:00:00:00:11"),
    ("sb", "10.0.0.12", "00:00:00:00:00:12"),
    ("sc", "10.0.0.13", "00:00:00:00:00:13"),
]

# Which client talks to which server. Each pair is one concurrent flow.
FLOWS = [
    # (flow_name, client_name, server_name, server_ip, server_port)
    ("f1_ca_sa", "ca", "sa", "10.0.0.11", 5005),
    ("f2_cb_sb", "cb", "sb", "10.0.0.12", 5005),
    ("f3_cc_sc", "cc", "sc", "10.0.0.13", 5005),
]


def controller_is_up(url="http://127.0.0.1:8080/sdn/status"):
    try:
        with urllib.request.urlopen(url, timeout=2) as resp:
            return resp.status == 200
    except Exception:
        return False


def build_net(primary_loss, datapath):
    net = Mininet(controller=None, switch=OVSSwitch, link=TCLink)
    c0 = net.addController("c0", controller=RemoteController, ip="127.0.0.1", port=6653)

    hosts = {}
    for name, ip, mac in CLIENTS + SERVERS:
        hosts[name] = net.addHost(name, ip="%s/24" % ip, mac=mac)

    s1 = net.addSwitch("s1", protocols="OpenFlow13", datapath=datapath)
    s2 = net.addSwitch("s2", protocols="OpenFlow13", datapath=datapath)
    s3 = net.addSwitch("s3", protocols="OpenFlow13", datapath=datapath)
    s4 = net.addSwitch("s4", protocols="OpenFlow13", datapath=datapath)

    # --- Trunk links (order fixes the port numbers the controller expects) ---
    net.addLink(s1, s2, port1=1, port2=1)                      # s1:1 <-> s2:1
    net.addLink(s1, s3, port1=2, port2=1)                      # s1:2 <-> s3:1
    net.addLink(s2, s4, port1=2, port2=1, loss=primary_loss)   # s2:2 <-> s4:1  PRIMARY (lossy)
    net.addLink(s3, s4, port1=2, port2=2)                      # s3:2 <-> s4:2  BACKUP (clean)

    # --- Client links on s1 (ports 3,4,5) ---
    net.addLink(hosts["ca"], s1, port1=0, port2=3)
    net.addLink(hosts["cb"], s1, port1=0, port2=4)
    net.addLink(hosts["cc"], s1, port1=0, port2=5)

    # --- Server links on s4 (ports 3,4,5) ---
    net.addLink(hosts["sa"], s4, port1=0, port2=3)
    net.addLink(hosts["sb"], s4, port1=0, port2=4)
    net.addLink(hosts["sc"], s4, port1=0, port2=5)

    net.build()
    c0.start()
    for sw in (s1, s2, s3, s4):
        sw.start([c0])

    # Static ARP for every pair so there is NO broadcast flooding (the physical
    # loop between the two paths would otherwise storm). Fixed MACs make this easy.
    ip_to_mac = {ip: mac for _n, ip, mac in CLIENTS + SERVERS}
    for name, ip, _mac in CLIENTS + SERVERS:
        for other_ip, other_mac in ip_to_mac.items():
            if other_ip != ip:
                hosts[name].setARP(ip=other_ip, mac=other_mac)

    return net, hosts


def main():
    parser = argparse.ArgumentParser(description="Multi-client/multi-server packet-loss-and-recovery demo.")
    parser.add_argument("--loss", type=float, default=30.0, help="Primary path loss %%")
    parser.add_argument("--count", type=int, default=2000, help="Packets each client sends")
    parser.add_argument("--rate", type=float, default=50.0, help="Packets per second per client")
    parser.add_argument("--threshold", type=float, default=0.15, help="Loss ratio to trigger reroute")
    parser.add_argument("--switch", choices=["user", "kernel"], default="kernel")
    parser.add_argument("--no-reroute", action="store_true",
                        help="Baseline: detect loss but DON'T reroute (for comparison)")
    args = parser.parse_args()

    setLogLevel("info")

    if not controller_is_up():
        print("!!! Ryu controller is not reachable at 127.0.0.1:8080.")
        print("!!! Start it first in another terminal:")
        print("!!!     ~/ryu-venv/bin/ryu-manager ryu_controller_multi.py")
        return

    # Begin on the primary path, clear any stale signal file.
    try:
        urllib.request.urlopen(
            urllib.request.Request("http://127.0.0.1:8080/sdn/primary", data=b"", method="POST"),
            timeout=3)
    except Exception:
        pass
    if os.path.exists(SIGNAL_FILE):
        os.remove(SIGNAL_FILE)

    info("*** Building multi-host network (primary loss = %s%%, datapath = %s)\n"
         % (args.loss, args.switch))
    net, hosts = build_net(args.loss, args.switch)
    time.sleep(5)  # let switches connect and flows install

    # Connectivity check: each client should reach its server.
    for _fname, cname, _sname, sip, _sport in FLOWS:
        ping = hosts[cname].cmd("ping -c 2 -W 1 %s" % sip)
        ok = "0% packet loss" in ping or " 0 received" not in ping
        info("*** %s -> %s : %s\n" % (cname, sip, "reachable" if "bytes from" in ping else "NO REPLY"))

    # Fresh stats folder so an old run does not pollute the aggregate.
    stats_dir = os.path.join(HERE, "stats_multi")
    if os.path.isdir(stats_dir):
        shutil.rmtree(stats_dir)
    os.makedirs(stats_dir)

    info("*** Starting reroute agent (root namespace)\n")
    agent = subprocess.Popen(
        ["python3", os.path.join(HERE, "reroute_agent.py")],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT)

    no_reroute_flag = "--no-reroute" if args.no_reroute else ""
    recv_py = os.path.join(HERE, "udp_receiver.py")
    send_py = os.path.join(HERE, "udp_sender.py")

    # --- Start all SERVERS (receivers) first. ---
    info("*** Starting %d servers (receivers)\n" % len(FLOWS))
    for fname, _cname, sname, _sip, sport in FLOWS:
        stats_file = os.path.join(stats_dir, "%s.receiver.json" % fname)
        out = os.path.join(stats_dir, "%s.receiver.txt" % fname)
        hosts[sname].cmd(
            "python3 -u %s --port %d --loss-threshold %s --run-time 120 "
            "--flow-name %s --stats-file %s %s > %s 2>&1 &"
            % (recv_py, sport, args.threshold, fname, stats_file, no_reroute_flag, out))
    time.sleep(1)

    # --- Start all CLIENTS (senders) at the same time, in the background. ---
    info("*** Starting %d clients (senders) concurrently\n" % len(FLOWS))
    for fname, cname, _sname, sip, sport in FLOWS:
        stats_file = os.path.join(stats_dir, "%s.sender.json" % fname)
        out = os.path.join(stats_dir, "%s.sender.txt" % fname)
        hosts[cname].cmd(
            "python3 -u %s --dst-ip %s --dst-port %d --listen-port 5006 "
            "--count %d --rate %s --flow-name %s --stats-file %s > %s 2>&1 &"
            % (send_py, sip, sport, args.count, args.rate, fname, stats_file, out))

    # Let every flow run to completion (send time + retransmission tail + margin).
    run_seconds = (args.count / args.rate) + 20
    info("*** All flows running on the PRIMARY path; loss will trigger ONE reroute\n")
    info("*** Waiting ~%.0f s for all flows to finish...\n" % run_seconds)
    time.sleep(run_seconds)

    agent.terminate()
    net.stop()

    # --- Statistics. ---
    print("\n\n################## MULTI-FLOW RESULTS ##################\n")
    subprocess.run(["python3", os.path.join(HERE, "collect_stats.py"),
                    "--stats-dir", stats_dir])
    print("\n(Per-flow raw reports are in %s/*.txt)" % stats_dir)


if __name__ == "__main__":
    main()
