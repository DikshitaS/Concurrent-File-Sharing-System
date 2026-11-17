"""OS-Ken SDN controller: h2 is the file server; block h3 -> h2."""
import time
from os_ken.base import app_manager
from os_ken.controller import ofp_event
from os_ken.controller.handler import CONFIG_DISPATCHER, MAIN_DISPATCHER, DEAD_DISPATCHER, set_ev_cls
from os_ken.ofproto import ofproto_v1_3
from os_ken.lib.packet import packet, ethernet, ether_types
from os_ken.lib import hub

class SDNController(app_manager.OSKenApp):
    OFP_VERSIONS = [ofproto_v1_3.OFP_VERSION]
    SERVER_IP = "10.0.0.2"
    BLOCKED_CLIENT_IP = "10.0.0.3"

    def __init__(self, *args, **kwargs):
        super(SDNController, self).__init__(*args, **kwargs)
        self.mac_to_port = {}
        self._datapaths = {}
        self._port_stats = {}
        self.monitor_thread = hub.spawn(self.monitor)

    @set_ev_cls(ofp_event.EventOFPSwitchFeatures, CONFIG_DISPATCHER)
    def switch_features(self, ev):
        dp = ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        # Drop IPv4 packets from h3 (10.0.0.3) to server h2 (10.0.0.2).
        match = parser.OFPMatch(eth_type=ether_types.ETH_TYPE_IP,
                                ipv4_src=self.BLOCKED_CLIENT_IP,
                                ipv4_dst=self.SERVER_IP)
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=200,
                                      match=match, instructions=[]))
        # Send unmatched packets to the controller.
        actions = [parser.OFPActionOutput(ofp.OFPP_CONTROLLER, ofp.OFPCML_NO_BUFFER)]
        instructions = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
        dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=0,
                                      match=parser.OFPMatch(), instructions=instructions))
        self.logger.info("Switch %s connected; ACL blocks h3 %s -> h2 server %s",
                         dp.id, self.BLOCKED_CLIENT_IP, self.SERVER_IP)

    @set_ev_cls(ofp_event.EventOFPStateChange, [MAIN_DISPATCHER, DEAD_DISPATCHER])
    def switch_state_change(self, ev):
        dp = ev.datapath
        if ev.state == MAIN_DISPATCHER:
            self._datapaths[dp.id] = dp
            self.logger.info("Registered switch %s for port monitoring", dp.id)
        elif ev.state == DEAD_DISPATCHER:
            self._datapaths.pop(dp.id, None)
            self._port_stats.pop(dp.id, None)

    @set_ev_cls(ofp_event.EventOFPPacketIn, MAIN_DISPATCHER)
    def packet_in(self, ev):
        msg, dp = ev.msg, ev.msg.datapath
        ofp, parser = dp.ofproto, dp.ofproto_parser
        in_port = msg.match["in_port"]
        pkt = packet.Packet(msg.data)
        eth = pkt.get_protocol(ethernet.ethernet)
        if eth is None or eth.ethertype == ether_types.ETH_TYPE_LLDP:
            return
        self.mac_to_port.setdefault(dp.id, {})
        self.mac_to_port[dp.id][eth.src] = in_port
        out_port = self.mac_to_port[dp.id].get(eth.dst, ofp.OFPP_FLOOD)
        actions = [parser.OFPActionOutput(out_port)]
        if out_port != ofp.OFPP_FLOOD:
            match = parser.OFPMatch(in_port=in_port, eth_src=eth.src, eth_dst=eth.dst)
            inst = [parser.OFPInstructionActions(ofp.OFPIT_APPLY_ACTIONS, actions)]
            dp.send_msg(parser.OFPFlowMod(datapath=dp, priority=10, match=match,
                                          instructions=inst, idle_timeout=60))
        data = None if msg.buffer_id != ofp.OFP_NO_BUFFER else msg.data
        dp.send_msg(parser.OFPPacketOut(datapath=dp, buffer_id=msg.buffer_id,
                                        in_port=in_port, actions=actions, data=data))

    def monitor(self):
        while True:
            for dp in list(self._datapaths.values()):
                req = dp.ofproto_parser.OFPPortStatsRequest(dp, 0, dp.ofproto.OFPP_ANY)
                dp.send_msg(req)
            hub.sleep(5)

    @set_ev_cls(ofp_event.EventOFPPortStatsReply, MAIN_DISPATCHER)
    def port_stats_reply(self, ev):
        dp, now = ev.msg.datapath, time.monotonic()
        for stat in ev.msg.body:
            key = (dp.id, stat.port_no)
            old = self._port_stats.get(key)
            self._port_stats[key] = (now, stat.rx_bytes, stat.tx_bytes)
            if old is None:
                continue
            old_time, old_rx, old_tx = old
            elapsed = now - old_time
            if elapsed <= 0:
                continue
            rx = max(0, (stat.rx_bytes - old_rx) * 8 / elapsed / 1_000_000)
            tx = max(0, (stat.tx_bytes - old_tx) * 8 / elapsed / 1_000_000)
            self.logger.info("Switch %s port %s | RX: %.3f Mbps | TX: %.3f Mbps | RX bytes: %s | TX bytes: %s",
                             dp.id, stat.port_no, rx, tx, stat.rx_bytes, stat.tx_bytes)
