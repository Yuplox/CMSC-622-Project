"""
server32.py — Use Case 3.2 Reliable Multicast server, instrumented for experiments.

Env vars:
  STATS_FILE  path to write JSON stats on exit  (default /tmp/server32_stats.json)
  DURATION    seconds to run before self-terminating  (default 30)
"""

import os
import signal
import socket
import struct
import sys
import threading
import time
from collections import OrderedDict

from gf256 import gf_encode
from metrics import Stats

# ── Network constants ──────────────────────────────────────────────────────────
MULTICAST_GROUP  = ('224.0.0.1', 10000)
NACK_PORT        = 9001
REPAIR_MAGIC     = 0xFFFFFFFF

# ── Tuning knobs ───────────────────────────────────────────────────────────────
STREAM_INTERVAL  = 0.5
NACK_AGGREGATION = 1.0
WINDOW_SIZE      = 64

# ── Config from env ────────────────────────────────────────────────────────────
STATS_FILE = os.environ.get('STATS_FILE', '/tmp/server32_stats.json')
DURATION   = float(os.environ.get('DURATION', '30'))

# ── Shared state ───────────────────────────────────────────────────────────────
window_lock   = threading.Lock()
packet_window = OrderedDict()
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


def pad_to(data, length):
    # type: (bytes, int) -> bytes
    return data.ljust(length, b'\x00')


def make_mcast_socket(server_ip):
    # type: (str) -> socket.socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((server_ip, 0))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF,
                    socket.inet_aton(server_ip))
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL,
                    struct.pack('b', 4))
    return sock


# ── Stream thread ──────────────────────────────────────────────────────────────

class StreamThread(threading.Thread):
    CORPUS = [
        b"The quick brown fox jumps over the lazy dog near the riverbank.",
        b"Pack my box with five dozen liquor jugs for the celebration now.",
        b"How vexingly quick daft zebras jump over the wooden fence today.",
        b"The five boxing wizards jump quickly past the startled audience.",
        b"Sphinx of black quartz, judge my vow under the pale moonlight!!",
        b"Jackdaws love my big sphinx of quartz sitting on the hilltop up.",
        b"The jay, pig, fox, zebra and my wolves quack in the cold fog up.",
        b"Blowzy red vixens fight for a quick jump over the white fence!!.",
    ]
    PKT_LEN = max(len(s) for s in CORPUS)

    def __init__(self, sock):
        super(StreamThread, self).__init__()
        self.daemon = True
        self.sock   = sock
        self.seq    = 0

    def run(self):
        print("[stream] Starting data stream...")
        while not stop_event.is_set():
            payload = pad_to(self.CORPUS[self.seq % len(self.CORPUS)], self.PKT_LEN)

            with window_lock:
                packet_window[self.seq] = payload
                if len(packet_window) > WINDOW_SIZE:
                    packet_window.popitem(last=False)

            wire = struct.pack('!II', self.seq, len(payload)) + payload
            try:
                self.sock.sendto(wire, MULTICAST_GROUP)
                stats.record_send(len(wire))
                print("[stream] seq={:4d}  '{}'".format(
                    self.seq, payload.rstrip(b'\x00').decode()[:40]
                ))
            except OSError as e:
                print("[stream] sendto error (seq={}): {}".format(self.seq, e))

            self.seq += 1
            time.sleep(STREAM_INTERVAL)


# ── Repair thread ──────────────────────────────────────────────────────────────

class RepairThread(threading.Thread):
    def __init__(self, mcast_sock, server_ip):
        super(RepairThread, self).__init__()
        self.daemon     = True
        self.mcast_sock = mcast_sock

        self.nack_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.nack_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.nack_sock.bind((server_ip, NACK_PORT))
        self.nack_sock.settimeout(NACK_AGGREGATION)

    def run(self):
        print("[repair] Listening for NACKs on port {}...".format(NACK_PORT))
        pending = set()

        while not stop_event.is_set():
            deadline = time.time() + NACK_AGGREGATION
            while time.time() < deadline:
                try:
                    raw, addr = self.nack_sock.recvfrom(4096)
                    # NACK bytes are counted as received control traffic
                    stats.record_recv(len(raw))
                    count = len(raw) // 4
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


# ── Entry point ────────────────────────────────────────────────────────────────

def run_server(server_ip):
    # type: (str) -> None
    signal.signal(signal.SIGTERM, shutdown)
    print("[server32] Starting on {} (duration={}s)".format(server_ip, DURATION))

    mcast_sock = make_mcast_socket(server_ip)

    StreamThread(mcast_sock).start()
    RepairThread(mcast_sock, server_ip).start()

    stop_event.wait(DURATION)
    stop_event.set()

    print("[server32] Duration elapsed, saving stats.")
    stats.save(STATS_FILE)
    mcast_sock.close()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 server32.py [SERVER_IP]")
        sys.exit(1)
    run_server(sys.argv[1])
