"""
terminal32.py — Reliable Multicast terminal (Use Case 3.2) — streaming edition.

Three concurrent threads run forever:

  RecvThread   — joins the multicast group and receives all packets.
                 Data packets are stored in a local buffer indexed by seq_num.
                 Repair packets are handed to try_recover().

  NackThread   — periodically scans the received sequence numbers for gaps,
                 sends a NACK to the server for each missing sequence number
                 that is still within the repair window.

  try_recover  — on receiving a repair packet, builds the GF(2^8) linear
                 system from locally buffered packets and solves for the
                 missing ones via Gaussian elimination.

Wire formats  (must match server32.py)
────────────
Data packet   — seq(4) | payload_len(4) | payload
Repair packet — 0xFFFFFFFF(4) | N(4) | N*(seq(4)+coeff(1)) | payload

Usage
─────
  python3 terminal32.py SERVER_IP TERMINAL_IP [NACK_INTERVAL] [NACK_WINDOW] [LABEL]

  NACK_INTERVAL  seconds between gap-scan cycles  (default 2.0)
  NACK_WINDOW    how many seq numbers back we bother NACKing  (default 32)
  LABEL          log prefix  (default 'term')
"""

import socket
import struct
import sys
import threading
import time
from collections import OrderedDict

from gf256 import gf_solve, gf_scale, gf_add_packets

# ── Network constants (must match server32) ───────────────────────────────────
MULTICAST_ADDRESS = '224.0.0.1'
MULTICAST_PORT    = 10000
NACK_PORT         = 9001
REPAIR_MAGIC      = 0xFFFFFFFF

# ── Defaults (overridable via argv) ───────────────────────────────────────────
DEFAULT_NACK_INTERVAL = 2.0
DEFAULT_NACK_WINDOW   = 32

# ── Shared state ──────────────────────────────────────────────────────────────
buf_lock = threading.Lock()
recv_buf = OrderedDict()   # seq_num -> bytes

# Repair rows keyed by frozenset of encoded seq IDs.
# Each entry is a list of (coeff_dict, payload) tuples.
repair_lock = threading.Lock()
repair_buf  = {}   # frozenset -> list of (dict, bytes)

highest_seq = -1   # highest seq number seen so far (data packets only)


def decode_data_wire(raw):
    # type: (bytes) -> tuple
    """Parse a data packet. Returns (seq, payload) or raises ValueError."""
    if len(raw) < 8:
        raise ValueError("Data packet too short")
    seq, plen = struct.unpack_from('!II', raw, 0)
    payload = raw[8: 8 + plen]
    return seq, payload


def decode_repair_wire(raw):
    # type: (bytes) -> tuple
    """
    Parse a repair packet (magic already confirmed by caller).
    Returns (encoded_ids, coeffs, payload).
      encoded_ids : list of int
      coeffs      : dict mapping seq ID -> GF(2^8) coefficient
      payload     : bytes
    """
    if len(raw) < 8:
        raise ValueError("Repair packet too short")
    n = struct.unpack_from('!I', raw, 4)[0]
    header_size = 8 + n * 5
    if len(raw) < header_size:
        raise ValueError("Repair packet truncated")
    encoded_ids = []
    coeffs = {}
    off = 8
    for _ in range(n):
        sid, c = struct.unpack_from('!IB', raw, off)
        encoded_ids.append(sid)
        coeffs[sid] = c
        off += 5
    payload = raw[header_size:]
    return encoded_ids, coeffs, payload


def try_recover(encoded_ids, pkt_len, label):
    # type: (list, int, str) -> None
    """
    Attempt to recover all missing packets among encoded_ids using
    whatever repair rows have been collected so far.
    """
    global recv_buf, repair_buf

    with buf_lock:
        missing = [s for s in encoded_ids if s not in recv_buf]
        have    = {s: recv_buf[s] for s in encoded_ids if s in recv_buf}

    if not missing:
        return

    key = frozenset(encoded_ids)
    with repair_lock:
        rows = list(repair_buf.get(key, []))

    if len(rows) < len(missing):
        return   # not enough repair rows yet

    # Build the linear system:
    #   repair_r = sum_j( c_r_j * P_j )
    # Rearrange so only unknowns remain on the RHS:
    #   sum_{j in missing} c_r_j * X_j = repair_r XOR sum_{j in have} c_r_j * P_j
    coeff_matrix = []
    rhs_list     = []

    for coeffs, payload in rows[:len(missing)]:
        adj = bytearray(payload)
        for sid, pdata in have.items():
            c = coeffs.get(sid, 0)
            if c:
                adj = bytearray(gf_add_packets(bytes(adj), gf_scale(pdata, c)))
        rhs_list.append(bytes(adj))
        coeff_matrix.append([coeffs.get(s, 0) for s in missing])

    try:
        recovered = gf_solve(coeff_matrix, rhs_list, pkt_len)
    except ValueError as e:
        print("  [{}] Solve failed: {}".format(label, e))
        return

    with buf_lock:
        for sid, pdata in zip(missing, recovered):
            recv_buf[sid] = pdata
            text = pdata.rstrip(b'\x00').decode('utf-8', errors='replace')
            print("  [{}] RECOVERED seq={}: '{}'".format(label, sid, text))


# ── Receive thread ─────────────────────────────────────────────────────────────

class RecvThread(threading.Thread):
    def __init__(self, term_ip, label):
        super(RecvThread, self).__init__()
        self.daemon  = True
        self.term_ip = term_ip
        self.label   = label

    def run(self):
        global highest_seq

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('', MULTICAST_PORT))

        mreq = struct.pack("4s4s",
                           socket.inet_aton(MULTICAST_ADDRESS),
                           socket.inet_aton(self.term_ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)

        print("[{}] Joined multicast group {}:{}".format(
            self.label, MULTICAST_ADDRESS, MULTICAST_PORT
        ))

        while True:
            try:
                raw, addr = sock.recvfrom(8192)
            except Exception as e:
                print("[{}] recv error: {}".format(self.label, e))
                continue

            if len(raw) < 4:
                continue

            magic_candidate = struct.unpack_from('!I', raw, 0)[0]

            if magic_candidate == REPAIR_MAGIC:
                try:
                    encoded_ids, coeffs, payload = decode_repair_wire(raw)
                except ValueError as e:
                    print("[{}] Bad repair packet: {}".format(self.label, e))
                    continue

                key = frozenset(encoded_ids)
                with repair_lock:
                    repair_buf.setdefault(key, []).append((coeffs, payload))

                print("[{}] Repair received  seqs={}  coeffs={}".format(
                    self.label,
                    encoded_ids,
                    ['0x{:02x}'.format(coeffs[s]) for s in encoded_ids]
                ))

                try_recover(encoded_ids, len(payload), self.label)

            else:
                try:
                    seq, payload = decode_data_wire(raw)
                except ValueError as e:
                    print("[{}] Bad data packet: {}".format(self.label, e))
                    continue

                with buf_lock:
                    if seq not in recv_buf:
                        recv_buf[seq] = payload
                        highest_seq = max(highest_seq, seq)

                text = payload.rstrip(b'\x00').decode('utf-8', errors='replace')
                print("[{}] seq={:4d}  '{}'".format(self.label, seq, text[:50]))


# ── NACK thread ────────────────────────────────────────────────────────────────

class NackThread(threading.Thread):
    def __init__(self, server_ip, term_ip, label, nack_interval, nack_window):
        super(NackThread, self).__init__()
        self.daemon        = True
        self.server_ip     = server_ip
        self.label         = label
        self.nack_interval = nack_interval
        self.nack_window   = nack_window
        self.nacked        = set()
        # Bind to term_ip so the NACK leaves on the correct interface in Mininet
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((term_ip, 0))

    def run(self):
        print("[{}] NACK thread started  (interval={}s  window={})".format(
            self.label, self.nack_interval, self.nack_window
        ))
        while True:
            time.sleep(self.nack_interval)

            with buf_lock:
                hi   = highest_seq
                have = set(recv_buf.keys())

            if hi < 1:
                continue

            lo = max(0, hi - self.nack_window)
            missing     = [s for s in range(lo, hi) if s not in have]
            new_missing = [s for s in missing if s not in self.nacked]

            if not new_missing:
                continue

            print("[{}] Sending NACK for seqs {}".format(self.label, new_missing))
            wire = struct.pack('!{}I'.format(len(new_missing)), *new_missing)
            self.sock.sendto(wire, (self.server_ip, NACK_PORT))
            self.nacked.update(new_missing)


# ── Entry point ────────────────────────────────────────────────────────────────

def run_terminal(server_ip, term_ip, nack_interval, nack_window, label):
    RecvThread(term_ip, label).start()

    time.sleep(1)   # let recv thread join multicast before first NACK fires
    NackThread(server_ip, term_ip, label, nack_interval, nack_window).start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[{}] Shutting down.".format(label))


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 terminal32.py SERVER_IP TERMINAL_IP "
              "[NACK_INTERVAL] [NACK_WINDOW] [LABEL]")
        print("  NACK_INTERVAL  seconds between gap scans  (default 2.0)")
        print("  NACK_WINDOW    seq numbers back to NACK   (default 32)")
        print("  LABEL          log prefix                 (default 'term')")
        sys.exit(1)

    srv_ip  = sys.argv[1]
    term_ip = sys.argv[2]
    interval = float(sys.argv[3]) if len(sys.argv) > 3 else DEFAULT_NACK_INTERVAL
    window   = int(sys.argv[4])   if len(sys.argv) > 4 else DEFAULT_NACK_WINDOW
    label    = sys.argv[5]        if len(sys.argv) > 5 else 'term'

    run_terminal(srv_ip, term_ip, interval, window, label)
