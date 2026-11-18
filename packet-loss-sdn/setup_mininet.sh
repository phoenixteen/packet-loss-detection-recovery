#!/usr/bin/env bash
# =============================================================================
# setup_mininet.sh  --  NEEDS sudo (installs system packages)
# -----------------------------------------------------------------------------
# Installs Mininet + Open vSwitch and starts the OVS daemons.
#
# Run it yourself (it will prompt for your password):
#     bash setup_mininet.sh
#
# Notes for WSL2:
#   * WSL2 kernels usually DON'T have the openvswitch kernel module, so we use
#     OVS's USERSPACE (netdev) datapath -- topology.py defaults to --switch user.
#   * systemd is enabled on your WSL, so we start OVS via systemctl.
# =============================================================================
set -e

echo "### installing mininet + open vswitch (sudo)"
sudo apt-get update
sudo apt-get install -y mininet openvswitch-switch openvswitch-common iproute2 iputils-ping

echo "### starting Open vSwitch"
if pidof systemd >/dev/null 2>&1; then
    sudo systemctl enable --now openvswitch-switch || sudo service openvswitch-switch start
else
    sudo /usr/share/openvswitch/scripts/ovs-ctl start
fi

echo "### cleaning any stale Mininet state"
sudo mn -c >/dev/null 2>&1 || true

echo "### checks:"
echo -n "   mn:        "; mn --version 2>&1 | head -1
echo -n "   ovs:       "; sudo ovs-vsctl --version | head -1
echo    "   ovs-vsctl show:"; sudo ovs-vsctl show || true

echo
echo "DONE. Mininet + OVS ready (userspace datapath for WSL2)."
