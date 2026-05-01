from mininet.net import Mininet
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.cli import CLI
from shared import SatelliteTopo
import sys

def run(useCase):
    setLogLevel('info')

    terminalCount = 0
    if useCase == '3.1':
        terminalCount = 2
    elif useCase == '3.2':
        terminalCount = 10

    net = Mininet(topo=SatelliteTopo(10, 100, '250ms', 1, terminalCount), link=TCLink)
    net.start()

    server = net.get('ser0')

    if useCase == '3.1':
        server_ip = server.IP()

        termA  = net.get('term0')
        termB  = net.get('term1')
        termA_ip  = termA.IP()
        termB_ip  = termB.IP()

        server.cmd(f'python3 -u server31.py {server_ip} > server31.log 2>&1 &')
        termA.cmd(
            f'python3 -u terminal31.py {server_ip} {termA_ip} '
            f'"Hello from Terminal A! This is a test payload." > term-A-31.log 2>&1 &'
        )
        termB.cmd(
            f'python3 -u terminal31.py {server_ip} {termB_ip} '
            f'"Greetings from Terminal B! We are saving bandwidth." > term-B-31.log 2>&1 &'
        )

    elif useCase == '3.2':
        server_ip = server.IP()

        terminals = []
        for i in range(terminalCount):
            terminals.append(net.get(f'term{i}'))
            terminals[i].cmd(
                f'python3 -u terminal32.py {server_ip} {term.IP()} '
                f'> term-{i}-32.log 2>&1 &'
            )

        # Args: SERVER_IP  TERMINAL_IP  NACK_INTERVAL  NACK_WINDOW  LABEL
        server.cmd(f'python3 -u server32.py {server_ip} > server32.log 2>&1 &')

        for term in terminals:
            term.cmd(
                
            )

    CLI(net)
    net.stop()

if __name__ == '__main__':
    if len(sys.argv) == 2 and sys.argv[1] in ('3.1', '3.2'):
        run(sys.argv[1])
    else:
        print('Expected usage: run.py [USE_CASE]  (3.1 or 3.2)')
