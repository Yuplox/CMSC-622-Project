from mininet.topo import Topo

class SatelliteTopo(Topo):
    def build(self, bandwidth, feedBandwidth, delay, loss, termCount):
        terminals = []
        for i in range(termCount):
            terminals.append(self.addHost(f'term{i}'))
        
        satTerm2 = self.addHost('term1')
        server = self.addHost('ser0')

        gateway = self.addSwitch('gs0')
        satellite = self.addSwitch('sat0')

        # Terminal to Satellite (User links)
        for term in terminals:
            self.addLink(term, satellite, bw=bandwidth, delay=delay, loss=loss, use_htb=True)

        # Satellite to Gateway (Feeder Link)
        self.addLink(satellite, gateway, bw=feedBandwidth, delay=delay, loss=loss, use_htb=True)

        # Gateway to server (Fiber optic Link)
        self.addLink(gateway, server, bw=1000, delay='1ms', use_htb=True)

def xor_bytes(b1, b2):
    # Pad the shorter byte string with null bytes
    length = max(len(b1), len(b2))
    b1 = b1.ljust(length, b'\0')
    b2 = b2.ljust(length, b'\0')

    return bytes(x ^ y for x, y in zip(b1, b2))