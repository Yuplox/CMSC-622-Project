import os
import signal
import socket
import struct
import sys
import time
from datetime import datetime

from shared import *
from gf256 import gf_solve, gf_scale, gf_add_packets
from metrics import Stats


STATS_FILE = None
stats = None

# Give enough time for response to arrive before sending another NACK
NACK_COOLDOWN = NACK_AGGREGATION + 1



# Ensure STATS_FILE is saved even when terminated early
def shutdown(signum, frame):
    if STATS_FILE is not None:
        stats.save(STATS_FILE)
    sys.exit(0)


def try_recover(encoded_seqs, pkt_len, recv_buf, repair_buf, highest_seq, label, term_id):

    # Determine known and missing packets
    missing_seqs = [seq for seq in encoded_seqs if seq not in recv_buf]
    known_packets = {seq: recv_buf[seq] for seq in encoded_seqs if seq in recv_buf}

    # Return if we are not missing any packets
    if not missing_seqs:
        return highest_seq

    # Check that we have enough coded packets to solve for missing packets
    group_key = frozenset(encoded_seqs)
    coded_packets = repair_buf.get(group_key, [])

    # Return if not
    if len(coded_packets) < len(missing_seqs):
        return highest_seq

    coeff_matrix = []
    rhs_list = []

    # Only use exactly as many coded packets as we need to solve the matrix
    for coeffs, coded_payload in coded_packets[:len(missing_seqs)]:
        
        # Start with the raw coded payload (the right-hand side of the equation)
        adjusted_payload = bytearray(coded_payload)
        
        # Subtract the packets we already know from the coded payload
        for known_seq, known_data in known_packets.items():
            coeff = coeffs.get(known_seq, 0)
            if coeff != 0:
                scaled_known = gf_scale(known_data, coeff)
                adjusted_payload = bytearray(gf_add_packets(bytes(adjusted_payload), scaled_known))
        
        rhs_list.append(bytes(adjusted_payload))
        
        # Build the matrix row using only the coefficients of the MISSING packets
        row_coeffs = [coeffs.get(missing_seq, 0) for missing_seq in missing_seqs]
        coeff_matrix.append(row_coeffs)

    # Solve the matrix
    try:
        recovered_payloads = gf_solve(coeff_matrix, rhs_list, pkt_len)
    except ValueError as e:
        print(f"  [{label}-{term_id}] Solve failed: {e}")
        return highest_seq

    # Process recovered packets
    for seq, payload in zip(missing_seqs, recovered_payloads):
        recv_buf.add(seq, payload)
        highest_seq = max(highest_seq, seq)
        
        # Unpack the recovered payload to log it properly
        try:
            lat, lon, timestamp = GPSPayload.unpack_data(payload)

            # Calculate stats
            rtt = time.time() - timestamp
            stats.record_recv(len(payload))
            stats.record_rtt(rtt)
            
            print(f"  [{label}-{term_id}] RECOVERED seq={seq} rtt={rtt * 1000}ms lat={lat} lon={lon}")
        except Exception as e:
            print(f"  [{label}-{term_id}] RECOVERED seq={seq} (Failed to unpack GPS data: {e})")

    # Remove group key because matrix was solved
    repair_buf.remove(group_key)

    return highest_seq



def run_terminal(server_ip, term_ip, term_id, label="term32"):
    signal.signal(signal.SIGTERM, shutdown)

    global stats 
    stats = Stats('terminal', '{label}_{term_id}')

    print(f"[{label}-{term_id}] Starting (duration={DURATION}s)")
    
    # Create listen socket and join multicast group
    listen_socket = setup_socket('', MULTICAST_PORT)
    setup_multicast_client(listen_socket, term_ip, MULTICAST_IP)

    # Create another socket to send NACKs from
    send_socket = setup_socket(term_ip, CLIENT_PORT)
    
    # Create a sliding window to map sequences to payloads
    recv_window = SlidingWindow(WINDOW_SIZE)

    # Create a sliding window to map sequence sets to repair payloads
    repair_window = RepairWindow(WINDOW_SIZE)

    # Timestamps when NACK was sent
    nack_timestamps = {}

    deadline = time.time() + DURATION
    highest_seq = -1
    while time.time() < deadline:
        
        # Check for any packets broadcasted
        while(True):
            try:
                packet, addr = listen_socket.recvfrom(BUFF_SIZE)

                # Extract msg_type
                msg_type = CodingProtocol.check_msg(packet)
                
                if msg_type == MSG_DATA:
                    # Extract header and data from packet
                    seq_num, payload = CodingProtocol.unpack_data(packet)
                    lat, lon, timestamp = GPSPayload.unpack_data(payload)

                    # Add payload to buffer and update highest sequence
                    recv_window.add(seq_num, payload)
                    highest_seq = max(seq_num, highest_seq)

                    # Calculate stats
                    rtt = time.time() - timestamp
                    stats.record_recv(len(packet))
                    stats.record_rtt(rtt)
                    print(f'[{label}-{term_id}] seq={seq_num} rtt={rtt * 1000}ms  lat={lat} lon={lon} time={datetime.fromtimestamp(timestamp)}')

                elif msg_type == MSG_CODED:
                    coeffs, seq_nums, payload = CodingProtocol.unpack_coded_data(packet)

                    # Collect stats
                    stats.record_repair_recv(len(packet))

                    # Store coded packet for potential recovery
                    key = frozenset(seq_nums)
                    repair_window.add(key, coeffs, payload)
                    
                    # Attempt recovery
                    highest_seq = try_recover(seq_nums, len(payload), recv_window, repair_window, highest_seq, label, term_id)
                    print(f'[{label}-{term_id}] msg="Coded packet received" for seqs={seq_nums}')

            except socket.timeout:
                break


        current_time = time.time()
        missing_seqs = []
        lowest_seq = max(SEQUENCE_START, highest_seq - WINDOW_SIZE + 1)
        
        for seq in range(lowest_seq, highest_seq + 1):
            if seq not in recv_window:
                # Only NACK if we haven't asked recently
                last_nack_time = nack_timestamps.get(seq, 0)
                if current_time - last_nack_time > NACK_COOLDOWN:
                    missing_seqs.append(seq)
                    nack_timestamps[seq] = current_time

        # Clean up timestamps for packets we no longer care about or just recovered
        for seq in list(nack_timestamps.keys()):
            if seq < lowest_seq or seq in recv_window:
                del nack_timestamps[seq]

        # If there are missing packets, send out a NACK for them
        if missing_seqs:
            print(f"[{label}-{term_id}] Missing packets {missing_seqs}, sending NACK...")
            nack_packet = CodingProtocol.pack_nack(missing_seqs) 
            send_socket.sendto(nack_packet, (server_ip, SERVER_NACK_PORT))

        time.sleep(SLEEP_INTERVAL)

    send_socket.close()
    listen_socket.close()
    print(f"[{label}-{term_id}] Duration elapsed, shutting down.")

    # Calculate total expected packets based on the highest sequence number seen
    if highest_seq >= SEQUENCE_START:
        total_expected = (highest_seq - SEQUENCE_START) + 1
        stats.record_expected(total_expected)

    if STATS_FILE is not None:
        stats.save(STATS_FILE)
    sys.exit(0)


if __name__ == '__main__':
    if len(sys.argv) < 4:
        print("Usage: python3 terminal32.py SERVER_IP TERMINAL_IP TERMINAL_ID")
        sys.exit(1)

    # Check for optional stats file
    if len(sys.argv) == 5:
        STATS_FILE = sys.argv[4]

    time.sleep(0.1) # Wait 100ms for server to initialize
    run_terminal(sys.argv[1], sys.argv[2], sys.argv[3])