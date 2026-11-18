#!/usr/bin/env python3
r"""
ryu_controller.py
-----------------
A Ryu SDN application for the "Packet Loss Detection and Recovery" project.

The topology (see topology.py) is a DIAMOND with two paths between h1 and h2:

        s2
       /  \
    h1-s1   s4-h2          PRIMARY path : s1 -> s2 -> s4
       \  /                BACKUP  path : s1 -> s3 -> s4
        s3

This controller is PROACTIVE and STATIC: it installs exact forwarding rules
for whichever path is currently active, matching on the destination MAC
address. Because the hosts use fixed MACs and static ARP (set up in
topology.py), there is no broadcast flooding, so the physical loop between the
two paths never causes a storm.

It also runs a small REST API so the control loop can switch paths at runtime:

    POST /sdn/reroute   -> switch traffic to the BACKUP path
    POST /sdn/primary   -> switch traffic back to the PRIMARY path
    GET  /sdn/status    -> report which path is active

Run it with:
    ryu-manager ryu_controller.py
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.lib.packet import packet, ethernet
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from webob import Response
import json


# Host MAC addresses. Mininet with --mac assigns these predictably.
H1_MAC = "00:00:00:00:00:01"
H2_MAC = "00:00:00:00:00:02"

# Datapath IDs (Mininet numbers s1..s4 as 1..4).
S1, S2, S3, S4 = 1, 2, 3, 4

# Port layout, fixed by the order links are added in topology.py.
#   s1: 1->h1, 2->s2, 3->s3
#   s2: 1->s1, 2->s4
#   s3: 1->s1, 2->s4
#   s4: 1->s2, 2->s3, 3->h2
PORTS = {
    S1: {"h1": 1, "s2": 2, "s3": 3},
    S2: {"s1": 1, "s4": 2},
    S3: {"s1": 1, "s4": 2},
    S4: {"s2": 1, "s3": 2, "h2": 3},
}

# A name the REST layer uses to find our app instance.
CONTROLLER_INSTANCE = "packet_loss_sdn_app"


class PacketLossSDN(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(PacketLossSDN, self).__init__(*args, **kwargs)

        # Remember each switch's datapath object so REST calls can push flows.
        self.datapaths = {}

        # Which path is active right now.
        self.active_path = "primary"

        # Register the REST controller and hand it a reference to ourselves.
        wsgi = kwargs["wsgi"]
        wsgi.register(RerouteRestController, {CONTROLLER_INSTANCE: self})

    # ------------------------------------------------------------------ setup
    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features_handler(self, ev):
        """Called once per switch when it connects. Install its flows."""
        datapath = ev.msg.datapath
        self.datapaths[datapath.id] = datapath

        self.logger.info("Switch connected: dpid=%s", datapath.id)
        self.install_path_flows(datapath, self.active_path)

    # --------------------------------------------------------------- flow mgmt
    def install_path_flows(self, datapath, path):
        """
        Install the forwarding rules for one switch, given the active path.
        Only s1 and s4 actually choose between primary/backup; s2 and s3 just
        forward straight through.
        """
        dpid = datapath.id

        if dpid == S1:
            # Toward h1: always out the h1 port.
            self.add_flow(datapath, H1_MAC, PORTS[S1]["h1"])
            # Toward h2: out via s2 (primary) or s3 (backup).
            out = PORTS[S1]["s2"] if path == "primary" else PORTS[S1]["s3"]
            self.add_flow(datapath, H2_MAC, out)

        elif dpid == S4:
            # Toward h2: always out the h2 port.
            self.add_flow(datapath, H2_MAC, PORTS[S4]["h2"])
            # Toward h1: out via s2 (primary) or s3 (backup).
            out = PORTS[S4]["s2"] if path == "primary" else PORTS[S4]["s3"]
            self.add_flow(datapath, H1_MAC, out)

        elif dpid == S2:
            self.add_flow(datapath, H1_MAC, PORTS[S2]["s1"])
            self.add_flow(datapath, H2_MAC, PORTS[S2]["s4"])

        elif dpid == S3:
            self.add_flow(datapath, H1_MAC, PORTS[S3]["s1"])
            self.add_flow(datapath, H2_MAC, PORTS[S3]["s4"])

    def add_flow(self, datapath, eth_dst, out_port):
        """Install one flow: match on destination MAC, output to one port."""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch(eth_dst=eth_dst)
        actions = [parser.OFPActionOutput(out_port)]
        instructions = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        # A new ADD with the same match replaces the old one (OFPFF_ overwrite),
        # so re-installing during a reroute cleanly updates the output port.
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=10,
            match=match,
            instructions=instructions,
        )
        datapath.send_msg(mod)

    # ----------------------------------------------------------- reroute logic
    def switch_path(self, new_path):
        """Re-install the choosing switches (s1, s4) onto the requested path."""
        if new_path not in ("primary", "backup"):
            return False

        self.active_path = new_path

        for dpid in (S1, S4):
            datapath = self.datapaths.get(dpid)
            if datapath is not None:
                self.install_path_flows(datapath, new_path)

        self.logger.info("=== Traffic switched to %s path ===", new_path.upper())
        return True


# ---------------------------------------------------------------------------
# REST API: lets the detection/control loop switch paths over HTTP.
# ---------------------------------------------------------------------------
class RerouteRestController(ControllerBase):
    def __init__(self, req, link, data, **config):
        super(RerouteRestController, self).__init__(req, link, data, **config)
        self.app = data[CONTROLLER_INSTANCE]

    @route("sdn", "/sdn/status", methods=["GET"])
    def status(self, req, **kwargs):
        body = json.dumps({"active_path": self.app.active_path})
        return self._json(body)

    @route("sdn", "/sdn/reroute", methods=["POST"])
    def reroute(self, req, **kwargs):
        ok = self.app.switch_path("backup")
        body = json.dumps({"ok": ok, "active_path": self.app.active_path})
        return self._json(body)

    @route("sdn", "/sdn/primary", methods=["POST"])
    def primary(self, req, **kwargs):
        ok = self.app.switch_path("primary")
        body = json.dumps({"ok": ok, "active_path": self.app.active_path})
        return self._json(body)

    @staticmethod
    def _json(body):
        # webob needs bytes (or an explicit charset) for the response body.
        return Response(content_type="application/json; charset=utf-8",
                        body=body.encode("utf-8"))
