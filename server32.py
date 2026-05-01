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


REPAIR_MAGIC     = 0xFFFFFFFF
STATS_FILE = None


NACK_AGGREGATION = 1.0


# Shared variables
window_lock   = threading.Lock()
packet_window = SlidingWindow()
stats         = Stats('server', 'server32')
stop_event    = threading.Event()


def shutdown(signum, frame):
    stop_event.set()


def random_nonzero_coeff():
    # type: () -> int
    while True:
        b = os.urandom(1)[0]
        if b:
            return b


class StreamThread(threading.Thread):
    def __init__(self, sock):
        super(StreamThread, self).__init__()
        self.daemon = True
        self.sock = sock
        self.protocol = TerminalProtocol()

    def run(self):
        print("[stream] Starting data stream...")
        while not stop_event.is_set():

            # Create packet with GPS data
            payload = GPSPayload.pack_data()
            packet = protocol.pack_data(MSG_DATA, payload)
            seq_num = protocol.seq.curr_val()

            with window_lock:
                packet_window.add(seq_num, payload)

            # Send packet to multicast group
            try:
                self.sock.sendto(packet, MULTICAST_GROUP)
                stats.record_send(len(packet))
                print(f"[stream] sent {len(packet)} bytes to {MULTICAST_GROUP}")
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
        print("[repair] Listening for NACKs on port {}...".format(NACK_PORT))
        pending = set()

        while not stop_event.is_set():
            deadline = time.time() + NACK_AGGREGATION
            while time.time() < deadline:
                try:
                    nack, addr = self.nack_sock.recvfrom(BUFF_SIZE)
                    
                    stats.record_recv(len(nack))
                    count = len(nack) // 4
                    seqs  = list(struct.unpack('!{}I'.format(count), raw[:count * 4]))
                    print("[repair] NACK from {}: missing seqs {}".format(addr, seqs))
                    pending.update(seqs)
                except socket.timeout:
                    break

            if not pending:
                continue

            with window_lock:
                available = {s: packet_window[s] for s in pending if s in packet_window}

            expired = pending - set(available)
            if expired:
                print("[repair] WARNING: seqs {} expired from window.".format(sorted(expired)))

            if not available:
                pending.clear()
                continue

            encoded_ids = sorted(available)
            num_repairs = len(encoded_ids)
            print("[repair] Sending {} repair packet(s) for seqs {}...".format(
                num_repairs, encoded_ids
            ))

            for _ in range(num_repairs):
                coeffs  = {sid: random_nonzero_coeff() for sid in encoded_ids}
                payload = gf_encode(available, coeffs)

                header = struct.pack('!II', REPAIR_MAGIC, len(encoded_ids))
                for sid in encoded_ids:
                    header += struct.pack('!IB', sid, coeffs[sid])
                wire = header + payload

                try:
                    self.mcast_sock.sendto(wire, MULTICAST_GROUP)
                    # Each repair is a retransmission — counted separately
                    stats.record_repair_sent(len(wire))
                    coeff_str = ' '.join(
                        'seq{}*0x{:02x}'.format(s, coeffs[s]) for s in encoded_ids
                    )
                    print("[repair]   sent: {}  ({}B)".format(coeff_str, len(wire)))
                except OSError as e:
                    print("[repair]   sendto error: {}".format(e))

            pending.clear()


def run_server(server_ip, lable="server32"):
    signal.signal(signal.SIGTERM, shutdown)

    print(f"[{label}] starting on {server_ip} (duration={DURATION}s)")

    # Create server socket
    server_sock = setup_socket(server_ip, SERVER_PORT)
    setup_multicast_server(server_sock)

    # Create socket to receive NACKs
    nack_sock = setup_socket(server_ip, SERVER_NACK_PORT)

    # Create threads
    StreamThread(server_sock).start()
    RepairThread(server_sock, nack_sock).start()

    # Set stop flag after duaration elapses
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
    run_server(sys.argv[1])
