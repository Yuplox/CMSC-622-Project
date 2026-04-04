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
        server_ip = server.IP()
        termA_ip = termA.IP()
        termB_ip = termB.IP()

        server.cmd(f'python3 -u server31.py {server_ip} > server.log 2>&1 &')
        termA.cmd(f'python3 -u terminal31.py {server_ip} {termA_ip} "Hello from Terminal A! This is a test payload." > termA.log 2>&1 &')
        termB.cmd(f'python3 -u terminal31.py {server_ip} {termB_ip} "Greetings from Terminal B! We are saving bandwidth." > termB.log 2>&1 &')

    elif (useCase == '3.2'):
        print('3.2 is not implemented yet')

    # Start mininet command line
    CLI(net)

    # Stop mininet after exiting command line
    net.stop()

if __name__ == '__main__':
    if len(sys.argv) == 2 and (sys.argv[1] == '3.1' or sys.argv[1] == '3.2'):
        run(sys.argv[1])
    else:
        print('Expected usage: run.py [USE_CASE]')
