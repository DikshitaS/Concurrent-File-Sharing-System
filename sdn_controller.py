import time
"""
SDN controller for the Concurrent File Sharing System.

Policy:
- Block IPv4 traffic from h3 (10.0.0.3) to the file server h1 (10.0.0.1).
- Allow other traffic using a simple MAC-learning switch.

Run from the OS-Ken virtual environment:
    PYTHONPATH=$HOME:$PYTHONPATH osken-manager sdn_controller
"""

from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import (
    CONFIG_DISPATCHER,
    MAIN_DISPATCHER,
    DEAD_DISPATCHER,
    set_ev_cls,
)
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib.packet import packet, ethernet
from os_ken.lib.packet import ether_types
from os_ken.lib import hub


class SDNController(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]

    SERVER_IP = "10.0.0.1"
    BLOCKED_CLIENT_IP = "10.0.0.3"

    def __init__(self, *args, **kwargs):
        super(SDNController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self._datapaths = {}
        self._port_stats = {}
        self.monitor_thread = hub.spawn(self.monitor)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features(self, ev):
        datapath = ev.msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser

        # Priority 200: block h3 -> h1 IPv4 traffic.
        match = parser.OFPMatch(
            eth_type=ether_types.ETH_TYPE_IP,
            ipv4_src=self.BLOCKED_CLIENT_IP,
            ipv4_dst=self.SERVER_IP,
        )
        drop = parser.OFPFlowMod(
            datapath=datapath,
            priority=200,
            match=match,
            instructions=[],
        )
        datapath.send_msg(drop)

        # Table-miss: send unmatched packets to the controller.
        match = parser.OFPMatch()
        actions = [
            parser.OFPActionOutput(
                ofproto.OFPP_CONTROLLER,
                ofproto.OFPCML_NO_BUFFER,
            )
        ]
        instructions = [
            parser.OFPInstructionActions(
                ofproto.OFPIT_APPLY_ACTIONS, actions
            )
        ]
        table_miss = parser.OFPFlowMod(
            datapath=datapath,
            priority=0,
            match=match,
            instructions=instructions,
        )
        datapath.send_msg(table_miss)

        self.logger.info(
            "Switch %s connected; installed ACL h3 (%s) -> h1 (%s)",
            datapath.id,
            self.BLOCKED_CLIENT_IP,
            self.SERVER_IP,
        )

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def switch_state_change(self, ev):
        datapath = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self._datapaths[datapath.id] = datapath
            self.logger.info("Registered switch %s for port monitoring", datapath.id)
        elif ev.state == DEAD_DISPATCHER:
            self._datapaths.pop(datapath.id, None)
            self._port_stats.pop(datapath.id, None)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg = ev.msg
        datapath = msg.datapath
        ofproto = datapath.ofproto
        parser = datapath.ofproto_parser
        in_port = msg.match["in_port"]

        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None:
            return

        # Ignore LLDP packets.
        if eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return

        dpid = datapath.id
        self.mac_to_port.setdefault(dpid, {})
        self.mac_to_port[dpid][eth.src] = in_port

        out_port = self.mac_to_port[dpid].get(eth.dst)
        if out_port is None:
            out_port = ofproto.OFPP_FLOOD

        actions = [parser.OFPActionOutput(out_port)]

        # Install a flow for known unicast destinations.
        if out_port != ofproto.OFPP_FLOOD:
            match = parser.OFPMatch(
                in_port=in_port,
                eth_src=eth.src,
                eth_dst=eth.dst,
            )
            instructions = [
                parser.OFPInstructionActions(
                    ofproto.OFPIT_APPLY_ACTIONS, actions
                )
            ]
            flow_mod = parser.OFPFlowMod(
                datapath=datapath,
                priority=10,
                match=match,
                instructions=instructions,
                idle_timeout=60,
            )
            datapath.send_msg(flow_mod)

        data = None if msg.buffer_id != ofproto.OFP_NO_BUFFER else msg.data
        packet_out = parser.OFPPacketOut(
            datapath=datapath,
            buffer_id=msg.buffer_id,
            in_port=in_port,
            actions=actions,
            data=data,
        )
        datapath.send_msg(packet_out)

    def monitor(self):
        while True:
            for datapath in list(self._datapaths.values()):
                parser = datapath.ofproto_parser
                request = parser.OFPPortStatsRequest(
                    datapath, 0, datapath.ofproto.OFPP_ANY
                )
                datapath.send_msg(request)
            hub.sleep(5)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply(self, ev):
        datapath = ev.msg.datapath
        dpid = datapath.id
        now = time.monotonic()

        for stat in ev.msg.body:
            key = (dpid, stat.port_no)
            previous = self._port_stats.get(key)
            self._port_stats[key] = (
                now,
                stat.rx_bytes,
                stat.tx_bytes,
            )

            if previous is None:
                continue

            old_time, old_rx, old_tx = previous
            elapsed = now - old_time
            if elapsed <= 0:
                continue

            rx_mbps = (stat.rx_bytes - old_rx) * 8 / elapsed / 1_000_000
            tx_mbps = (stat.tx_bytes - old_tx) * 8 / elapsed / 1_000_000

            self.logger.info(
                "Switch %s port %s | RX: %.3f Mbps | TX: %.3f Mbps "
                "| RX bytes: %s | TX bytes: %s",
                dpid,
                stat.port_no,
                max(rx_mbps, 0),
                max(tx_mbps, 0),
                stat.rx_bytes,
                stat.tx_bytes,
            )
