"""Mininet topology: h2 (10.0.0.2) is the file server; h3 is restricted."""
from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info

def main():
    net = Mininet(controller=None, switch=OVSSwitch, build=False, autoSetMacs=True)
    info("*** Adding remote OS-Ken controller\\n")
    c0 = net.addController("c0", controller=RemoteController,
                           ip="127.0.0.1", port=6653)
    info("*** Adding OpenFlow 1.3 switch and hosts\\n")
    s1 = net.addSwitch("s1", protocols="OpenFlow13", failMode="secure")
    h1 = net.addHost("h1", ip="10.0.0.1/24")  # client
    h2 = net.addHost("h2", ip="10.0.0.2/24")  # file server
    h3 = net.addHost("h3", ip="10.0.0.3/24")  # restricted client
    net.addLink(h1, s1)
    net.addLink(h2, s1)
    net.addLink(h3, s1)
    info("*** Starting network\\n")
    net.build()
    c0.start()
    s1.start([c0])
    info("*** Ready. Test commands:\\n")
    info("  nodes\\n  h1 ping -c 4 h2  (should work)\\n")
    info("  h3 ping -c 4 h2  (should be blocked)\\n")
    info("  sh ovs-ofctl -O OpenFlow13 dump-flows s1\\n")
    info("  sh ovs-ofctl -O OpenFlow13 dump-ports s1\\n")
    try:
        CLI(net)
    finally:
        info("*** Stopping network\\n")
        net.stop()

if __name__ == "__main__":
    setLogLevel("info")
    main()
