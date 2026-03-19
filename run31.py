from mininet.net import Mininet
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI
from shared import SatelliteTopo

def main():
    setLogLevel('info')

    info('*** Instantiating Network\n')
    net = Mininet(topo=SatelliteTopo(10, 100, '250ms', 1), link=TCLink)

    info('*** Starting Network\n')
    net.start()

    info('*** Testing connectivity and latency\n')
    net.pingAll()

    info('*** Running CLI\n')
    CLI(net)

    info('*** Stopping Network\n')
    net.stop()

if __name__ == '__main__':
    main()
