"""
Mininet topology for the Concurrent File Sharing System.

h1 = file server: 10.0.0.1
h2 = allowed client: 10.0.0.2
h3 = blocked client: 10.0.0.3
s1 = Open vSwitch switch

Run:
    sudo python3 topology.py
"""

from mininet.net import Mininet
from mininet.node import RemoteController, OVSSwitch
from mininet.cli import CLI
from mininet.log import setLogLevel, info


def main():
    net = Mininet(
        controller=None,
        switch=OVSSwitch,
        build=False,
        autoSetMacs=True,
    )

    info("*** Adding remote SDN controller\n")
    controller = net.addController(
        "c0",
        controller=RemoteController,
        ip="127.0.0.1",
        port=6653,
    )

    info("*** Adding switch and hosts\n")
    switch = net.addSwitch(
        "s1",
        protocols="OpenFlow13",
        failMode="secure",
    )

    h1 = net.addHost("h1", ip="10.0.0.1/24")
    h2 = net.addHost("h2", ip="10.0.0.2/24")
    h3 = net.addHost("h3", ip="10.0.0.3/24")

    info("*** Creating links\n")
    net.addLink(h1, switch)
    net.addLink(h2, switch)
    net.addLink(h3, switch)

    info("*** Starting network\n")
    net.build()
    controller.start()
    switch.start([controller])

    info("*** Network ready. Useful commands:\n")
    info("  nodes\n")
    info("  net\n")
    info("  h2 ping -c 4 h1       (should work)\n")
    info("  h3 ping -c 4 h1       (should be blocked)\n")
    info("  sh ovs-ofctl -O OpenFlow13 dump-flows s1\n")
    info("  sh ovs-ofctl -O OpenFlow13 dump-ports s1\n")

    try:
        CLI(net)
    finally:
        info("*** Stopping network\n")
        net.stop()


if __name__ == "__main__":
    setLogLevel("info")
    main()
