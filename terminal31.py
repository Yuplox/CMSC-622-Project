import os
import signal
import socket
import struct
import sys
import time
from datetime import datetime

from shared import *
from metrics import Stats

STATS_FILE = None
stats = Stats('server', 'terminal31')

# Ensure STATS_FILE is saved even when terminated early
def shutdown(signum, frame):
    if STATS_FILE is not None:
        stats.save(STATS_FILE)
    sys.exit(0)


def run_terminal(server_ip, term_ip, term_id, label="terminal31"):
    signal.signal(signal.SIGTERM, shutdown)
    
    # Create listen socket and join multicast group
    listen_socket = setup_socket('', MULTICAST_PORT)
    setup_multicast_client(listen_socket, term_ip, MULTICAST_IP)

    # Create another socket to send data from
    send_socket = setup_socket(term_ip, CLIENT_PORT)

    # Create terminal protocol instance to track sequence numbers
    protocol = TerminalProtocol()

    # Create sliding window instance to map sequence numbers to payloads
    window = SlidingWindow(WINDOW_SIZE)

    deadline = time.time() + DURATION
    while time.time() < deadline:

        # Create packet with random GPS coordinates in payload
        # Header contains the sequence number for the packet
        payload = GPSPayload.pack_data()
        packet = protocol.pack_data(MSG_DATA, payload)
        window.add(protocol.seq.curr_val(), payload)

        # Send the packet
        send_socket.sendto(packet, (server_ip, SERVER_PORT))

        # Calculate stats
        stats.record_send(len(packet))
        stats.record_expected(1)

        # Check for coded packets
        while(True):
            try:
                coded_packet, addr = listen_socket.recvfrom(BUFF_SIZE)

                # Extract header info
                seq_nums, coded_payload = ServerProtocol.unpack_data(coded_packet)
                termA_seq_num, termB_seq_num = seq_nums
                
                # Swap sequence numbers if this is term1
                if (term_id == "1"):
                    termA_seq_num, termB_seq_num = termB_seq_num, termA_seq_num
                
                # Decode the payload
                previous_payload = window.get_and_remove(termA_seq_num)
                if previous_payload is None:
                    print(f'[{label}-{term_id}] msg="Sequence number was missing from sliding window, so packet could not be decoded"')
                    continue

                decoded = xor_bytes(previous_payload, coded_payload)
                lat, lon, timestamp = GPSPayload.unpack_data(decoded)

                # Calculate stats
                rtt = time.time() - timestamp
                stats.record_recv(len(coded_packet))
                stats.record_rtt(rtt)

                # Print the decoded payload
                print(f"[{label}-{term_id}] seq={termB_seq_num} rtt={rtt * 1000}ms  lat={lat} lon={lon} time={datetime.fromtimestamp(timestamp)}")

            except socket.timeout:
                break
        
        # Check for any packets that failed to get coded
        while(True):
            try:
                not_coded_packet, addr = send_socket.recvfrom(BUFF_SIZE)

                # Extract header info and payload
                _, seq_num, not_coded_payload = TerminalProtocol.unpack_data(not_coded_packet)
                lat, lon, timestamp = GPSPayload.unpack_data(not_coded_payload)

                # Calculate stats
                rtt = time.time() - timestamp
                stats.record_recv(len(not_coded_payload))
                stats.record_rtt(rtt)

                # Print the decoded payload
                print(f'[{label}-{term_id}] msg="Non-coded packet received" seq={seq_num} rtt={rtt * 1000}ms  lat={lat} lon={lon} time={datetime.fromtimestamp(timestamp)}')

            except socket.timeout:
                break

        time.sleep(SLEEP_INTERVAL)

    send_socket.close()
    listen_socket.close()
    print(f"[{label}-{term_id}] Duration elapsed, shutting down.")

    if STATS_FILE is not None:
        stats.save(STATS_FILE)
    sys.exit(0)


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("Usage: python3 terminal31.py SERVER_IP TERMINAL_IP TERMINAL_ID")
        sys.exit(1)

    # Check for optional stats file
    if len(sys.argv) == 5:
        STATS_FILE = sys.argv[4]

    time.sleep(0.1) # Wait 100ms for server to initialize
    run_terminal(sys.argv[1], sys.argv[2], sys.argv[3])
