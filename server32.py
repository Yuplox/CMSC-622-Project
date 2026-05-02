import os
import signal
import socket
import struct
import sys
import threading
import time

from shared import *
from gf256 import gf_encode
from metrics import Stats

# Assumes CodingProtocol, SlidingWindow, GPSPayload are imported via shared, 
# or import them directly here if they are in separate files.

STATS_FILE = None
stats = None

# Shared variables
window_lock   = threading.Lock()
packet_window = SlidingWindow(WINDOW_SIZE)
protocol      = CodingProtocol()
stop_event    = threading.Event()


def shutdown(signum, frame):
    stop_event.set()


def random_nonzero_coeff():
    while True:
        b = os.urandom(1)[0]
        if b:
            return b


class StreamThread(threading.Thread):
    def __init__(self, sock):
        super(StreamThread, self).__init__()
        self.daemon = True
        self.sock = sock

    def run(self):
        print("[stream] Starting data stream...")
        while not stop_event.is_set():

            # Create payload with GPS data
            payload = GPSPayload.pack_data()
            
            # Create packet with sequence number and payload
            packet = protocol.pack_data(payload)
            seq_num = protocol.seq.curr_val()

            with window_lock:
                packet_window.add(seq_num, payload)

            # Send packet to multicast group
            try:
                self.sock.sendto(packet, MULTICAST_GROUP)
                stats.record_send(len(packet))
                print(f"[stream] seq={seq_num} sent {len(packet)} bytes to {MULTICAST_GROUP}")
            except OSError as e:
                print(f"[stream] sendto error (seq={seq_num}): {e}")

            time.sleep(SLEEP_INTERVAL)


class RepairThread(threading.Thread):
    def __init__(self, server_sock, nack_sock):
        super(RepairThread, self).__init__()
        self.daemon = True
        self.server_sock = server_sock 
        self.nack_sock = nack_sock

    def run(self):
        print(f"[repair] Listening for NACKs on port {SERVER_NACK_PORT}...")
        pending = set()

        while not stop_event.is_set():
            deadline = time.time() + NACK_AGGREGATION
            while time.time() < deadline:
                try:
                    packet, addr = self.nack_sock.recvfrom(BUFF_SIZE)
                    
                    # Parse 
                    msg_type = CodingProtocol.check_msg(packet)
                    if msg_type == MSG_NACK:
                        seqs = CodingProtocol.unpack_nack(packet)
                        stats.record_recv(len(packet))
                        print(f"[repair] NACK from {addr}: missing seqs {seqs}")
                        pending.update(seqs)
                        
                except socket.timeout:
                    break

            if not pending:
                continue

            with window_lock:
                # This works beautifully thanks to the __contains__ and __getitem__ methods!
                available = {s: packet_window[s] for s in pending if s in packet_window}

            expired = pending - set(available)
            if expired:
                print(f"[repair] WARNING: seqs {sorted(expired)} expired from window.")

            if not available:
                pending.clear()
                continue
            

            encoded_ids = sorted(available)
            num_repairs = len(encoded_ids)
            print(f"[repair] Sending {num_repairs} repair packet(s) for seqs {encoded_ids}...")

            for _ in range(num_repairs):
                
                # Build recipes for pack_coded_data, and a dict for gf_encode
                recipes = [(random_nonzero_coeff(), sid) for sid in encoded_ids]
                coeffs_dict = {seq_id: coeff for coeff, seq_id in recipes}
                
                # Create repair packet
                coded_payload = gf_encode(available, coeffs_dict)
                coded_packet = CodingProtocol.pack_coded_data(recipes, coded_payload)

                try:
                    # Send repair packet to multicast group
                    self.server_sock.sendto(coded_packet, MULTICAST_GROUP)

                    # Collect stats
                    stats.record_send(len(coded_packet)) 
                    
                    coeff_str = ' '.join(f'seq{s}*0x{c:02x}' for c, s in recipes)
                    print(f"[repair] sent: {coeff_str}  ({len(coded_packet)}B)")
                except OSError as e:
                    print(f"[repair] sendto error: {e}")

            pending.clear()


def run_server(server_ip, label="server32"):
    signal.signal(signal.SIGTERM, shutdown)

    global stats 
    stats = Stats('server', '{label}')

    print(f"[{label}] starting on {server_ip} (duration={DURATION}s)")

    # Create socket to stream packets from
    server_sock = setup_socket(server_ip, SERVER_PORT)
    setup_multicast_server(server_sock)

    # Create socket specifically to receive NACKs on the expected port
    nack_sock = setup_socket(server_ip, SERVER_NACK_PORT)

    # Create threads
    StreamThread(server_sock).start()
    RepairThread(server_sock, nack_sock).start()

    # Set stop flag after duration elapses
    stop_event.wait(DURATION)
    stop_event.set()

    # Wait for threads to complete before closing sockets
    time.sleep(0.1)
    server_sock.close()
    nack_sock.close()

    print(f"[{label}] Duration elapsed, saving stats.")

    if STATS_FILE is not None:
        stats.save(STATS_FILE)
    sys.exit(0)


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 server32.py [SERVER_IP]")
        sys.exit(1)

    # Check for optional stats file
    if len(sys.argv) == 3:
        STATS_FILE = sys.argv[2]

    run_server(sys.argv[1])