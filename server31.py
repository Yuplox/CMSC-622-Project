"""
server31.py — Use Case 3.1 Two-Way Relay server, instrumented for experiments.

Accepts STATS_FILE env var — path where JSON stats are written on SIGTERM/exit.
Also accepts DURATION env var — seconds to run before self-terminating.
"""

import os
import signal
import socket
import struct
import sys
import time

from shared import xor_bytes
from metrics import Stats

STATS_FILE = os.environ.get('STATS_FILE', '/tmp/server31_stats.json')
DURATION   = float(os.environ.get('DURATION', '30'))

stats = Stats('server', 'server31')


def shutdown(signum, frame):
    stats.save(STATS_FILE)
    sys.exit(0)


def run_server(host='0.0.0.0', port=9000):
    signal.signal(signal.SIGTERM, shutdown)

    server_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    server_socket.bind((host, port))
    server_socket.settimeout(1.0)

    # Multicast setup
    server_socket.setsockopt(
        socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(host)
    )
    server_socket.setsockopt(
        socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, struct.pack('b', 4)
    )

    multicast_group = ('224.0.0.1', 10000)
    print("[server31] Listening on {}:{} (duration={}s)".format(host, port, DURATION))

    clients = {}
    deadline = time.time() + DURATION

    while time.time() < deadline:
        try:
            data, addr = server_socket.recvfrom(2048)
        except socket.timeout:
            continue

        nbytes = len(data)
        stats.record_recv(nbytes)
        print("[server31] Received {} bytes from {}".format(nbytes, addr))

        clients[addr] = data

        if len(clients) == 2:
            addresses    = list(clients.keys())
            payload_A    = clients[addresses[0]]
            payload_B    = clients[addresses[1]]
            combined     = xor_bytes(payload_A, payload_B)

            # Prepend an 8-byte server send-timestamp (big-endian double) so
            # each terminal can compute true one-way multicast latency instead
            # of round-trip time that includes waiting for the other terminal.
            send_ts = time.time()
            wire = struct.pack('!d', send_ts) + combined

            server_socket.sendto(wire, multicast_group)
            stats.record_repair_sent(len(wire))
            print("[server31] XOR'd and broadcast {} bytes".format(len(wire)))

            clients.clear()

    print("[server31] Duration elapsed, shutting down.")
    stats.save(STATS_FILE)
    server_socket.close()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 server31.py [SERVER_IP]")
        sys.exit(1)
    run_server(sys.argv[1])
