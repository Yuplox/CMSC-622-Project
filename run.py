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

    # Get all hosts in topology
    server = net.get('ser0')
    termA = net.get('term0')
    termB = net.get('term1')


    if (useCase == '3.1'):
        # Start use case 3.1 scripts in the background
        server.cmd('python3 server31.py &')
        termA.cmd('python3 terminal31.py &')
        termB.cmd('python3 terminal31.py &')
    elif (useCase == '3.2'):
        # Simulate use case 3.2

    # Start mininet command line
    CLI(net)

    # Stop mininet after exiting command line
    net.stop()

if __name__ == '__main__':
    if len(sys.argv) == 2 and (sys.argv[1] == '3.1' || sys.argv[1] == '3.2'):
        run(sys.argv[1])
    else:
        print('Expected usage: run.py [USE_CASE]')
