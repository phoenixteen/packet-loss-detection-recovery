#!/usr/bin/env python3
r"""
ryu_controller_multi.py   (DELIVERABLE 2 - multi-client / multi-server)
-----------------------------------------------------------------------
The SAME SDN idea as ryu_controller.py, but generalised so that MANY clients
and MANY servers can share the one diamond network at the same time.

Topology (see topology_multi.py / run_demo_multi.py):

    clients          servers
    ca  cb  cc       sa  sb  sc
      \ | /            \ | /
        s1 --- s2 --- s4            PRIMARY path : s1 -> s2 -> s4  (lossy)
          \--- s3 ---/              BACKUP  path : s1 -> s3 -> s4  (clean)

Key point for the demo:
  * Forwarding is decided purely by the DESTINATION MAC address.
  * The primary/backup choice only depends on the DIRECTION (toward the server
    side, or back toward the client side) -- NOT on which host it is.
  * So a SINGLE reroute (re-installing s1 and s4) moves EVERY client<->server
    flow onto the clean backup path at once. One control action recovers all
    of them -- that is the strength of doing recovery in the SDN control plane.

REST API (same as before):
    POST /sdn/reroute   -> switch ALL traffic to the BACKUP path
    POST /sdn/primary   -> switch ALL traffic back to the PRIMARY path
    GET  /sdn/status    -> report which path is active

Run it with:
    ~/ryu-venv/bin/ryu-manager ryu_controller_multi.py
"""

from ryu.base import app_manager
from ryu.controller import ofp_event
from ryu.controller.handler import CONFIG_DISPATCHER, set_ev_cls
from ryu.ofproto import ofproto_v1_3
from ryu.app.wsgi import ControllerBase, WSGIApplication, route
from webob import Response
import json


# ---------------------------------------------------------------------------
# Hosts. These MUST match topology_multi.py / run_demo_multi.py.
#   CLIENTS hang off s1, SERVERS hang off s4.
# ---------------------------------------------------------------------------
CLIENT_MACS = {
    "ca": "00:00:00:00:00:01",
    "cb": "00:00:00:00:00:02",
    "cc": "00:00:00:00:00:03",
}
SERVER_MACS = {
    "sa": "00:00:00:00:00:11",
    "sb": "00:00:00:00:00:12",
    "sc": "00:00:00:00:00:13",
}

# Datapath IDs (Mininet numbers s1..s4 as 1..4).
S1, S2, S3, S4 = 1, 2, 3, 4

# Port layout, fixed by the order links are added in run_demo_multi.py.
#   s1: 1->s2(primary trunk), 2->s3(backup trunk), 3->ca, 4->cb, 5->cc
#   s2: 1->s1, 2->s4
#   s3: 1->s1, 2->s4
#   s4: 1->s2(primary trunk), 2->s3(backup trunk), 3->sa, 4->sb, 5->sc
PORTS = {
    S1: {"trunk_primary": 1, "trunk_backup": 2, "ca": 3, "cb": 4, "cc": 5},
    S2: {"s1": 1, "s4": 2},
    S3: {"s1": 1, "s4": 2},
    S4: {"trunk_primary": 1, "trunk_backup": 2, "sa": 3, "sb": 4, "sc": 5},
}

CONTROLLER_INSTANCE = "packet_loss_sdn_multi_app"


class PacketLossSDNMulti(app_manager.RyuApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    _CONTEXTS = {"wsgi": WSGIApplication}

    def __init__(self, *args, **kwargs):
        super(PacketLossSDNMulti, self).__init__(*args, **kwargs)
        self.datapaths = {}
        self.active_path = "primary"
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
        Only s1 and s4 choose between primary/backup; s2 and s3 forward straight
        through. The rules are the SAME for every host on a side, so adding more
        clients/servers is just more flow entries -- the path logic is unchanged.
        """
        dpid = datapath.id

        if dpid == S1:
            # Toward each client: out that client's own local port.
            for name, mac in CLIENT_MACS.items():
                self.add_flow(datapath, mac, PORTS[S1][name])
            # Toward every server: out via s2 (primary) or s3 (backup).
            out = PORTS[S1]["trunk_primary"] if path == "primary" else PORTS[S1]["trunk_backup"]
            for mac in SERVER_MACS.values():
                self.add_flow(datapath, mac, out)

        elif dpid == S4:
            # Toward each server: out that server's own local port.
            for name, mac in SERVER_MACS.items():
                self.add_flow(datapath, mac, PORTS[S4][name])
            # Toward every client: out via s2 (primary) or s3 (backup).
            out = PORTS[S4]["trunk_primary"] if path == "primary" else PORTS[S4]["trunk_backup"]
            for mac in CLIENT_MACS.values():
                self.add_flow(datapath, mac, out)

        elif dpid == S2:
            for mac in CLIENT_MACS.values():
                self.add_flow(datapath, mac, PORTS[S2]["s1"])
            for mac in SERVER_MACS.values():
                self.add_flow(datapath, mac, PORTS[S2]["s4"])

        elif dpid == S3:
            for mac in CLIENT_MACS.values():
                self.add_flow(datapath, mac, PORTS[S3]["s1"])
            for mac in SERVER_MACS.values():
                self.add_flow(datapath, mac, PORTS[S3]["s4"])

    def add_flow(self, datapath, eth_dst, out_port):
        """Install one flow: match on destination MAC, output to one port."""
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        match = parser.OFPMatch(eth_dst=eth_dst)
        actions = [parser.OFPActionOutput(out_port)]
        instructions = [parser.OFPInstructionActions(ofproto.OFPIT_APPLY_ACTIONS, actions)]

        # A new ADD with the same match replaces the old one, so re-installing
        # during a reroute cleanly updates the output port.
        mod = parser.OFPFlowMod(
            datapath=datapath,
            priority=10,
            match=match,
            instructions=instructions,
        )
        datapath.send_msg(mod)

    # ----------------------------------------------------------- reroute logic
    def switch_path(self, new_path):
        """Re-install the choosing switches (s1, s4) onto the requested path.
        This moves ALL client<->server flows at once."""
        if new_path not in ("primary", "backup"):
            return False

        self.active_path = new_path
        for dpid in (S1, S4):
            datapath = self.datapaths.get(dpid)
            if datapath is not None:
                self.install_path_flows(datapath, new_path)

        self.logger.info("=== ALL flows switched to %s path ===", new_path.upper())
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
