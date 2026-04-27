"""
terminal_nc.py — No-coding control baseline terminal.

Repeatedly sends a UDP message to the server and waits for the echo reply.
Measures RTT and loss without any network coding, providing the baseline
against which Use Cases 3.1 and 3.2 are compared.

Env vars:
  STATS_FILE  path to write JSON stats on exit  (default /tmp/term_nc_stats.json)
  DURATION    seconds to run  (default 30)
  LABEL       log/stats label  (default 'term_nc')
"""

import os
import signal
import socket
import sys
import time

from metrics import Stats

STATS_FILE = os.environ.get('STATS_FILE', '/tmp/term_nc_stats.json')
DURATION   = float(os.environ.get('DURATION', '30'))
LABEL      = os.environ.get('LABEL', 'term_nc')

stats = Stats('terminal', LABEL)


def shutdown(signum, frame):
    stats.save(STATS_FILE)
    sys.exit(0)


def run_terminal(server_ip, term_ip, msg):
    signal.signal(signal.SIGTERM, shutdown)

    server_port = 9000

    # Single socket for both send and receive: the server echoes to the source
    # port of each packet, so send and recv must share the same bound port.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((term_ip, 0))   # let OS pick an ephemeral port
    sock.settimeout(5.0)

    data      = msg.encode('utf-8')
    deadline  = time.time() + DURATION
    round_num = 0

    print("[{}] Starting (server={}:{} duration={}s)".format(
        LABEL, server_ip, server_port, DURATION))

    while time.time() < deadline:
        send_ts = time.time()
        sock.sendto(data, (server_ip, server_port))
        stats.record_send(len(data))
        stats.record_expected(1)

        try:
            echo, addr = sock.recvfrom(4096)
            rtt = time.time() - send_ts
            stats.record_recv(len(echo))
            stats.record_rtt(rtt)
            print("[{}] round={} RTT={:.3f}s  echo='{}'".format(
                LABEL, round_num, rtt, echo.decode('utf-8', errors='replace')[:40]
            ))
        except socket.timeout:
            print("[{}] round={} timed out".format(LABEL, round_num))

        round_num += 1
        time.sleep(1.0)

    print("[{}] Duration elapsed, shutting down.".format(LABEL))
    stats.save(STATS_FILE)
    sock.close()


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("Usage: python3 terminal_nc.py SERVER_IP TERMINAL_IP MESSAGE")
        sys.exit(1)
    time.sleep(1)  # wait for server to bind
    run_terminal(sys.argv[1], sys.argv[2], sys.argv[3])
