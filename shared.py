from mininet.topo import Topo

class SatelliteTopo(Topo):
    def build(self, bandwidth, feedBandwidth, delay, loss):
        satTerm1 = self.addHost('term0')
        satTerm2 = self.addHost('term1')
        server = self.addHost('ser0')

        gateway = self.addSwitch('gs0')
        satellite = self.addSwitch('sat0')

        # Terminal to Satellite (User links)
        self.addLink(satTerm1, satellite, bw=bandwidth, delay=delay, loss=loss, use_htb=True)
        self.addLink(satTerm2, satellite, bw=bandwidth, delay=delay, loss=loss, use_htb=True)

        # Satellite to Gateway (Feeder Link)
        self.addLink(satellite, gateway, bw=feedBandwidth, delay=delay, loss=loss, use_htb=True)

        # Gateway to server (Fiber optic Link)
        self.addLink(gateway, server, bw=1000, delay='1ms', use_htb=True)
