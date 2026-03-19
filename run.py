from mininet.net import Mininet
from mininet.link import TCLink
from mininet.log import setLogLevel, info
from mininet.cli import CLI
from shared import SatelliteTopo
import sys

def run(useCase):
    setLogLevel('info')

    net = Mininet(topo=SatelliteTopo(10, 100, '250ms', 1), link=TCLink)
    net.start()

    if (useCase == '3.1'):
        # Simulate use case 3.1;
    elif (useCase == '3.2'):
        # Simulate use case 3.2

    net.stop()

if __name__ == '__main__':
    if len(sys.argv) == 2 && (sys.argv[1] == '3.1' || sys.argv[1] == '3.2'):
        run(sys.argv[1])
    else:
        print('Expected usage: run.py [USE_CASE]')
