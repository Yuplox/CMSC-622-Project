from __future__ import annotations

import struct
import random
import socket
import time
from mininet.topo import Topo

# Shared variables
MULTICAST_IP = '224.0.0.1'
MULTICAST_PORT = 1000
MULTICAST_GROUP = (MULTICAST_IP, MULTICAST_PORT)
SERVER_PORT = 9000
SERVER_NACK_PORT = 9001
CLIENT_PORT = 8000

TTL = 4                 # Packets should never have more than 4 hops in our topology
SOCK_TIMEOUT = 0.01     # A short timeout is used to prevent sockets from blocking too long
RESPONSE_TIMEOUT = 2    # The rtt is about 1 second so responses should be received before twice that
BUFF_SIZE = 1024        # Packets should never be larger than 1024 bytes
DURATION = 30           # Each simulation lasts 30 seconds
WINDOW_SIZE = 50        # Keep the last 50 payloads for network coding
MSG_DATA = 1            # Identifies a packet as non-coded
MSG_CODED = 2           # Identifies a packet as coded
MSG_NACK = 3            # Identifies a NACK packet
MAX_HOLD_TIME = 0.05    # Max time to keep packets in the coding queue
SLEEP_INTERVAL = 0.25   # Time between sending packets

class SatelliteTopo(Topo):
    def build(self, bandwidth, feedBandwidth, delay, loss, termCount=2):
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


class Sequence:
    def __init__(self, start=1):
        self._seq_num = start

    def next_val(self):
        current = self._seq_num
        self._seq_num += 1
        return current
    
    def curr_val(self):
        return self._seq_num


class TerminalProtocol:
    HEADER_FORMAT = '!BI'
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

    def __init__(self):
        self.seq = Sequence()

    def pack_data(self, message_type, payload: bytes) -> bytes:
        seq_num = self.seq.next_val()
        header = struct.pack(TerminalProtocol.HEADER_FORMAT, message_type, seq_num)
        return header + payload

    def unpack_data(packet: bytes):
        header_bytes = packet[:TerminalProtocol.HEADER_SIZE]
        payload = packet[TerminalProtocol.HEADER_SIZE:]
        
        # Get the sequence number from the packet
        message_type, seq_num = struct.unpack(TerminalProtocol.HEADER_FORMAT, header_bytes)
        return message_type, seq_num, payload


class ServerProtocol:
    HEADER_FORMAT = '!II'
    HEADER_SIZE = struct.calcsize(HEADER_FORMAT)

    def pack_data(seq_nums: list, payload: bytes) -> bytes:
        header = struct.pack(ServerProtocol.HEADER_FORMAT, seq_nums[0], seq_nums[1])
        return header + payload

    def unpack_data(packet: bytes):
        header_bytes = packet[:ServerProtocol.HEADER_SIZE]
        payload = packet[ServerProtocol.HEADER_SIZE:]
        
        # Get the sequence number from the packet
        seq_nums = struct.unpack(ServerProtocol.HEADER_FORMAT, header_bytes)
        return seq_nums, payload

class GPSPayload:
    PAYLOAD_FORMAT = '!ffd'

    def get_random_coords() -> tuple[float, float]:
        lat = random.uniform(-90.0, 90.0)
        lon = random.uniform(-180.0, 180.0)
        return lat, lon

    def pack_data(data = None, timestamp = None) -> bytes:
        if data is None:
            lat, lon = GPSPayload.get_random_coords()
        else:
            lat, lon = data

        if timestamp is None:
            timestamp = time.time()
        
        return struct.pack(GPSPayload.PAYLOAD_FORMAT, lat, lon, timestamp)
        
    def unpack_data(payload) -> tuple[float, float, int]:
        return struct.unpack(GPSPayload.PAYLOAD_FORMAT, payload)


# Holds a map of sequence numbers to payloads
class SlidingWindow:
    def __init__(self, max_size: int):
        self.max_size = max_size
        self.buffer = {}  # dict to store {sequence_number: raw_payload}

    def add(self, seq_num: int, payload: bytes):
        self.buffer[seq_num] = payload
        
        # If we exceed the window size, remove the oldest entry
        if len(self.buffer) > self.max_size:
            oldest_seq = next(iter(self.buffer))
            self.buffer.pop(oldest_seq)

    def get_and_remove(self, seq_num: int) -> bytes:
        return self.buffer.pop(seq_num, None)

    def size(self) -> int:
        return len(self.buffer)


def xor_bytes(b1, b2):
    # Pad the shorter byte string with null bytes
    length = max(len(b1), len(b2))
    b1 = b1.ljust(length, b'\0')
    b2 = b2.ljust(length, b'\0')

    return bytes(x ^ y for x, y in zip(b1, b2))


def setup_socket(host, port) -> socket.socket:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.settimeout(SOCK_TIMEOUT)
    return sock


def setup_multicast_server(sock: socket.socket):
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack('b', TTL))


def setup_multicast_client(sock: socket.socket, terminal_ip, multicast_ip):
    mreq = struct.pack('4s4s', socket.inet_aton(multicast_ip), socket.inet_aton(terminal_ip))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)


def terminals_to_dict(terminals):
    parsed_terminals = {}
    for term in terminals:
        terminal_ip, terminal_id = term.split(":")
        parsed_terminals[terminal_id] = terminal_ip
    return parsed_terminals