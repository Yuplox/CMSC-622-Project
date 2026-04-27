"""
server32.py — Reliable Multicast coding server (Use Case 3.2) — streaming edition.

Two concurrent threads run forever:

  StreamThread  — broadcasts numbered data packets to the multicast group at a
                  fixed interval.  Every sent packet is stored in a sliding
                  window so it can be included in a repair later.

  RepairThread  — listens for NACKs from terminals on a dedicated UDP port.
                  When NACKs arrive it collects them for a short aggregation
                  window, then encodes all requested missing packet IDs into
                  GF(2^8) linear-combination repair packets and multicasts them.

Wire formats
────────────
Data packet  (multicast, port 10000):
  4B  seq_num  (uint32 big-endian)
  4B  payload length in bytes (uint32 big-endian)
  NB  payload

Repair packet (multicast, port 10000):
  4B  0xFFFFFFFF  — magic sentinel that distinguishes repairs from data
  4B  number of encoded packet IDs  (R, uint32)
  R x 5B  per encoded packet: 4B seq ID  +  1B GF(2^8) coefficient
  NB  linear-combination payload
"""

import os
import socket
import struct
import sys
import threading
import time
from collections import OrderedDict

from gf256 import gf_encode

# ── Network constants ──────────────────────────────────────────────────────────
MULTICAST_GROUP  = ('224.0.0.1', 10000)
NACK_PORT        = 9001
REPAIR_MAGIC     = 0xFFFFFFFF

# ── Tuning knobs ───────────────────────────────────────────────────────────────
STREAM_INTERVAL  = 0.5   # seconds between data packets
NACK_AGGREGATION = 1.0   # seconds to collect NACKs before sending repairs
WINDOW_SIZE      = 64    # max packets kept in memory for repair

# ── Shared state ───────────────────────────────────────────────────────────────
window_lock = threading.Lock()
packet_window = OrderedDict()  # seq_num -> bytes (padded payload)


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
    """
    Create a UDP socket configured for multicast sending inside a Mininet
    network namespace.

    Two settings are critical in Mininet:
      IP_MULTICAST_IF  — tells the kernel which interface to send multicast
                         traffic on.  Without this, Mininet's isolated namespace
                         has no default multicast route and raises ENETUNREACH.
      IP_MULTICAST_TTL — keeps packets alive long enough to cross the emulated
                         satellite hops (set to 4 for safety).
    The socket is also bound to the server's own IP so that the OS picks the
    correct source address when building the IP header.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Bind to server IP so the kernel knows the outgoing interface
    sock.bind((server_ip, 0))

    # Explicitly set the multicast output interface
    sock.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_MULTICAST_IF,
        socket.inet_aton(server_ip)
    )

    # Set TTL high enough to traverse all emulated hops
    sock.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_MULTICAST_TTL,
        struct.pack('b', 4)
    )

    return sock


# ── Stream thread ──────────────────────────────────────────────────────────────

class StreamThread(threading.Thread):
    """Continuously multicast sequentially numbered data packets."""

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
        while True:
            payload = pad_to(
                self.CORPUS[self.seq % len(self.CORPUS)], self.PKT_LEN
            )

            with window_lock:
                packet_window[self.seq] = payload
                if len(packet_window) > WINDOW_SIZE:
                    packet_window.popitem(last=False)

            # Wire: seq(4) | payload_len(4) | payload
            wire = struct.pack('!II', self.seq, len(payload)) + payload
            try:
                self.sock.sendto(wire, MULTICAST_GROUP)
                print("[stream] seq={:4d}  '{}'".format(
                    self.seq, payload.rstrip(b'\x00').decode()[:40]
                ))
            except OSError as e:
                print("[stream] sendto error (seq={}): {}".format(self.seq, e))

            self.seq += 1
            time.sleep(STREAM_INTERVAL)


# ── Repair thread ──────────────────────────────────────────────────────────────

class RepairThread(threading.Thread):
    """Listen for NACKs and respond with GF(2^8)-coded repair packets."""

    def __init__(self, mcast_sock, server_ip):
        super(RepairThread, self).__init__()
        self.daemon     = True
        self.mcast_sock = mcast_sock
        self.server_ip  = server_ip

        self.nack_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.nack_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.nack_sock.bind((server_ip, NACK_PORT))
        self.nack_sock.settimeout(NACK_AGGREGATION)

    def run(self):
        print("[repair] Listening for NACKs on {}:{}...".format(
            self.server_ip, NACK_PORT
        ))
        pending = set()

        while True:
            # Aggregate NACKs for one window
            deadline = time.time() + NACK_AGGREGATION
            while time.time() < deadline:
                try:
                    raw, addr = self.nack_sock.recvfrom(4096)
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
                print("[repair] WARNING: seqs {} no longer in window.".format(
                    sorted(expired)
                ))

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

                # Wire: magic(4) | N(4) | N*(seq(4)+coeff(1)) | payload
                header = struct.pack('!II', REPAIR_MAGIC, len(encoded_ids))
                for sid in encoded_ids:
                    header += struct.pack('!IB', sid, coeffs[sid])
                wire = header + payload

                try:
                    self.mcast_sock.sendto(wire, MULTICAST_GROUP)
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
    print("[server] Starting on {}".format(server_ip))

    mcast_sock = make_mcast_socket(server_ip)

    StreamThread(mcast_sock).start()
    RepairThread(mcast_sock, server_ip).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[server] Shutting down.")
        mcast_sock.close()


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("Usage: python3 server32.py [SERVER_IP]")
        sys.exit(1)
    run_server(sys.argv[1])
