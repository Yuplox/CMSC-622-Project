import os
import signal
import socket
import sys
import time

from shared import *
from metrics import Stats

STATS_FILE = None

stats = Stats('terminal', "term_nc")


# Ensure STATS_FILE is saved even when terminated early
def shutdown(signum, frame):
    if STATS_FILE is not None:
        stats.save(STATS_FILE)
    sys.exit(0)


def run_terminal(termA_ip, termB_ip, term_id, label="term_nc"):
    signal.signal(signal.SIGTERM, shutdown)

    # Create socket for both sending and receiving
    sock = setup_socket(termA_ip, CLIENT_PORT)

    print(f"[{label}] Starting no coding terminal (duration={DURATION}s)")

    while time.time() < deadline:

        # Create packet with random GPS coordinates in payload
        # Header contains the sequence number for the packet
        payload = GPSPayload.pack_data()
        packet = protocol.pack_data(payload)
        window.add(protocol.seq.curr_val(), payload)

        # Send the packet
        sock.sendto(packet, (termB_IP, CLIENT_PORT))

        # Calculate stats
        stats.record_send(len(packet))
        stats.record_expected(1)
        
        # Check for packets
        while(True):
            try:
                not_coded_packet, addr = sock.recvfrom(BUFF_SIZE)

                # Extract header info and payload
                seq_num, not_coded_payload = TerminalProtocol.unpack_data(not_coded_packet)
                lat, lon, timestamp = GPSPayload.unpack_data(not_coded_payload)

                # Calculate stats
                rtt = time.time() - timestamp
                stats.record_recv(len(not_coded_payload))
                stats.record_rtt(rtt)

                # Print the decoded payload
                print(f'[{label}-{term_id}] seq={seq_num} rtt={rtt * 1000}ms  lat={lat} lon={lon} time={datetime.fromtimestamp(timestamp)}')

            except socket.timeout:
                break

        time.sleep(SLEEP_INTERVAL)

    print("[{}] Duration elapsed, shutting down.")

    if STATS_FILE is not None:
        stats.save(STATS_FILE)
    sys.exit(0)


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("Usage: python3 terminal_nc.py TERMA_IP TERMB_IP TERMINAL_ID")
        sys.exit(1)

    # Check for optional stats file
    if len(sys.argv) == 5:
        STATS_FILE = sys.argv[4]

    time.sleep(0.1) # Wait 100ms for server to initialize
    run_terminal(sys.argv[1], sys.argv[2], sys.argv[3])