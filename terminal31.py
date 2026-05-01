"""
terminal31.py — Use Case 3.1 terminal, instrumented for experiments.

Env vars:
  STATS_FILE  path to write JSON stats (default /tmp/termX31_stats.json)
  DURATION    seconds to run repeated send/receive cycles (default 30)
  MSG         message to send each round (default hard-coded)
"""

import os
import signal
import socket
import struct
import sys
import time

from shared import xor_bytes
from metrics import Stats

STATS_FILE = os.environ.get('STATS_FILE', '/tmp/term31_stats.json')
DURATION   = float(os.environ.get('DURATION', '30'))
LABEL      = os.environ.get('LABEL', 'term31')

stats = Stats('terminal', LABEL)


def shutdown(signum, frame):
    stats.save(STATS_FILE)
    sys.exit(0)


def run_terminal(server_ip, term_ip, msg):
    signal.signal(signal.SIGTERM, shutdown)

    server_port       = 9000
    multicast_address = '224.0.0.1'
    multicast_port    = 10000

    data = msg.encode('utf-8')
    deadline = time.time() + DURATION
    round_num = 0

    # Open and join the multicast group once, outside the loop.
    # The server only fires a reply when it has received from *both* terminals,
    # so the reply for round N may arrive while we're already in round N+1.
    # A persistent socket buffers those replies; re-opening each round would
    # drop them and produce artificially inflated RTT or timeouts.
    listen_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    listen_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listen_socket.bind(('', multicast_port))
    mreq = struct.pack("4s4s",
                       socket.inet_aton(multicast_address),
                       socket.inet_aton(term_ip))
    listen_socket.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
    listen_socket.settimeout(5.0)

    send_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    send_socket.bind((term_ip, 0))

    while time.time() < deadline:
        # ── Send ──────────────────────────────────────────────────────────────
        send_ts = time.time()
        send_socket.sendto(data, (server_ip, server_port))
        stats.record_send(len(data))
        stats.record_expected(1)

        # ── Listen for coded reply ─────────────────────────────────────────────
        try:
            wire, addr = listen_socket.recvfrom(2048)
            rtt = time.time() - send_ts
            stats.record_recv(len(wire))
            stats.record_rtt(rtt)  # record_rtt stores seconds

            decoded = xor_bytes(wire, data).decode('utf-8', errors='replace').rstrip('\x00')
            print("[{}] round={} rtt={:.1f}ms  decoded='{}'".format(
                LABEL, round_num, rtt * 1000, decoded[:40]
            ))

        except socket.timeout:
            print("[{}] round={} timed out waiting for reply".format(LABEL, round_num))

        round_num += 1
        time.sleep(1.0)   # pace rounds so server can keep up

    send_socket.close()
    listen_socket.close()
    print("[{}] Duration elapsed, shutting down.".format(LABEL))
    stats.save(STATS_FILE)


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("Usage: python3 terminal31.py SERVER_IP TERMINAL_IP MESSAGE")
        sys.exit(1)

    time.sleep(1)   # wait for server
    run_terminal(sys.argv[1], sys.argv[2], sys.argv[3])
