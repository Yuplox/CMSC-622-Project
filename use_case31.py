from mininet.topo import Topo
from mininet.net import Mininet
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI

class SatelliteTopo(Topo):
    def build(self, bandwidth, delay, loss):
        satTerm1 = self.addHost('term0')
        satTerm2 = self.addHost('term1')
        gateway = self.addHost('gs0')
        satellite = self.addSwitch('sat0')

        self.addLink(satTerm1, satellite,
                     bw=bandwidth,
                     delay=delay,
                     loss=loss,
                     max_queue_size=1000,
                     use_htb=True)

        self.addLink(satTerm2, satellite,
                     bw=bandwidth,
                     delay=delay,
                     loss=loss,
                     max_queue_size=1000,
                     use_htb=True)

        self.addLink(gateway, satellite,
                     bw=bandwidth,
                     delay=delay,
                     loss=loss,
                     max_queue_size=1000,
                     use_htb=True)

def main():
    setLogLevel('info')

    info('*** Instantiating Network\n')
    net = Mininet(topo=SatelliteTopo(10, '250ms', 1), link=TCLink)

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