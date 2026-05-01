import os
import signal
import socket
import sys
import time

from metrics import Stats

STATS_FILE = os.environ.get('STATS_FILE', 'server_nc_stats.json')
DURATION   = float(os.environ.get('DURATION', '30'))

stats = Stats('server', 'server_nc')


def shutdown(signum, frame):
    stats.save(STATS_FILE)
    sys.exit(0)


def run_server(host='0.0.0.0', port=9000):
    signal.signal(signal.SIGTERM, shutdown)

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
    sock.settimeout(1.0)

    print("[server_nc] Listening on {}:{} (duration={}s)".format(host, port, DURATION))
    deadline = time.time() + DURATION

    while time.time() < deadline:
        try:
            data, addr = sock.recvfrom(4096)
        except socket.timeout:
            continue

        stats.record_recv(len(data))
        # Echo packet straight back to sender — no coding applied
        sock.sendto(data, addr)
        stats.record_send(len(data))
        print("[server_nc] echoed {} bytes to {}".format(len(data), addr))

    print("[server_nc] Duration elapsed, shutting down.")
    stats.save(STATS_FILE)
    sock.close()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 server_nc.py SERVER_IP")
        sys.exit(1)
    run_server(sys.argv[1])
