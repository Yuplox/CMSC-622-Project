import os
import signal
import socket
import struct
import sys
import time
from collections import deque 

from shared import *
from metrics import Stats

STATS_FILE = None
stats = None

# Ensure STATS_FILE is saved even when terminated early
def shutdown(signum, frame):
    if STATS_FILE is not None:
        stats.save(STATS_FILE)
    sys.exit(0)


def run_server(server_ip, terminals, label="server31"):
    signal.signal(signal.SIGTERM, shutdown)

    global stats 
    stats = Stats('server', '{label}')

    terminals = terminals_to_dict(terminals)

    # Setup up multicast server socket
    server_socket = setup_socket(server_ip, SERVER_PORT)
    setup_multicast_server(server_socket)

    print(f"[{label}] Listening on {server_ip}:{SERVER_PORT} (duration={DURATION}s)")

    clients = {}
    deadline = time.time() + DURATION
    while time.time() < deadline:
        try:
            # Receive packets from terminals first
            packet, addr = server_socket.recvfrom(BUFF_SIZE)
            stats.record_recv(len(packet))
            print(f"[server31] Received {len(packet)} bytes from {addr}")

            # Create queue for new clients
            ip, _ = addr
            if (ip not in clients):
                clients[ip] = deque()

            # Add tuple to client's queue with time received and data
            clients[ip].append((time.time(), packet))

        except socket.timeout:
            pass

        addresses = clients.keys()
        if len(addresses) == 2:

            # Maps terminal IDs to their corresponding addresses
            addr_A = terminals["0"]
            addr_B = terminals["1"]

            # If we have packets from two terminals we will XOR them
            while clients[addr_A] and clients[addr_B]:
                _, packet_A = clients[addr_A].popleft()
                _, packet_B = clients[addr_B].popleft()
                
                # Extract header data from packets
                seq_num_A, payload_A = TerminalProtocol.unpack_data(packet_A)
                seq_num_B, payload_B = TerminalProtocol.unpack_data(packet_B)

                # XOR payloads and create new header
                combined_payload = xor_bytes(payload_A, payload_B)
                combined_packet = ServerProtocol.pack_data((seq_num_A, seq_num_B), combined_payload)
                
                # Broadcast the combined packet
                server_socket.sendto(combined_packet, MULTICAST_GROUP)
                stats.record_repair_sent(len(combined_packet))
                print(f"[{label}] XOR'd and broadcast {len(combined_packet)} bytes")

            # Handle any assymetric traffic due to packet loss
            current_time = time.time()
            for addr, queue in clients.items():
                while queue and (current_time - queue[0][0]) > MAX_HOLD_TIME:
                    _, packet = queue.popleft()
                    
                    # Send packet directly to the other terminal
                    other_addr = [a for a in addresses if a != addr][0]
                    dest = (other_addr, CLIENT_PORT)
                    server_socket.sendto(packet, dest)

                    print(f"[{label}] Timeout: Sent {len(packet)} bytes uncoded from {addr}")

    server_socket.close()
    print(f"[{label}] Duration elapsed, shutting down.")

    if STATS_FILE is not None:
        stats.save(STATS_FILE)
    sys.exit(0)
    


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("Usage: python3 server31.py SERVER_IP TERMINAL_0 TERMINAL_1")
        sys.exit(1)

    # Check for optional stats file
    if len(sys.argv) == 5:
        STATS_FILE = sys.argv[4]
    
    run_server(sys.argv[1], (sys.argv[2], sys.argv[3]))
