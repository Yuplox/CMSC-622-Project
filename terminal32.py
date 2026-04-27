"""
terminal32.py — Use Case 3.2 Reliable Multicast terminal, instrumented for experiments.

Env vars:
  STATS_FILE     path to write JSON stats on exit  (default /tmp/termX32_stats.json)
  DURATION       seconds to run  (default 30)
  NACK_INTERVAL  seconds between gap scans  (default 2.0)
  NACK_WINDOW    seq numbers back to NACK  (default 32)
  LABEL          log/stats label  (default 'term32')
"""

import os
import signal
import socket
import struct
import sys
import threading
import time
from collections import OrderedDict

from gf256 import gf_solve, gf_scale, gf_add_packets
from metrics import Stats

# ── Network constants ──────────────────────────────────────────────────────────
MULTICAST_ADDRESS = '224.0.0.1'
MULTICAST_PORT    = 10000
NACK_PORT         = 9001
REPAIR_MAGIC      = 0xFFFFFFFF

# ── Config from env ────────────────────────────────────────────────────────────
STATS_FILE    = os.environ.get('STATS_FILE',    '/tmp/term32_stats.json')
DURATION      = float(os.environ.get('DURATION',      '30'))
NACK_INTERVAL = float(os.environ.get('NACK_INTERVAL', '2.0'))
NACK_WINDOW   = int(os.environ.get('NACK_WINDOW',   '32'))
LABEL         = os.environ.get('LABEL', 'term32')

# ── Shared state ───────────────────────────────────────────────────────────────
buf_lock    = threading.Lock()
recv_buf    = OrderedDict()
repair_lock = threading.Lock()
repair_buf  = {}
highest_seq      = -1   # highest seq ever placed in recv_buf (including recovered)
highest_wire_seq = -1   # highest seq received directly over the air (never recovered)
stop_event  = threading.Event()

stats = Stats('terminal', LABEL)


def shutdown(signum, frame):
    stop_event.set()


def decode_data_wire(raw):
    # type: (bytes) -> tuple
    if len(raw) < 8:
        raise ValueError("Data packet too short")
    seq, plen = struct.unpack_from('!II', raw, 0)
    payload = raw[8: 8 + plen]
    return seq, payload


def decode_repair_wire(raw):
    # type: (bytes) -> tuple
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


def try_recover(encoded_ids, pkt_len, recv_ts):
    # type: (list, int, float) -> None
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
        return

    coeff_matrix = []
    rhs_list     = []
    for coeffs, payload, send_ts in rows[:len(missing)]:
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
        print("  [{}] Solve failed: {}".format(LABEL, e))
        return

    rtt = time.time() - recv_ts
    stats.record_rtt(rtt)

    with buf_lock:
        for sid, pdata in zip(missing, recovered):
            recv_buf[sid] = pdata
            text = pdata.rstrip(b'\x00').decode('utf-8', errors='replace')
            print("  [{}] RECOVERED seq={}: '{}'".format(LABEL, sid, text[:50]))


# ── Receive thread ─────────────────────────────────────────────────────────────

class RecvThread(threading.Thread):
    def __init__(self, term_ip):
        super(RecvThread, self).__init__()
        self.daemon  = True
        self.term_ip = term_ip

    def run(self):
        global highest_seq, highest_wire_seq

        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(('', MULTICAST_PORT))
        sock.settimeout(1.0)

        mreq = struct.pack("4s4s",
                           socket.inet_aton(MULTICAST_ADDRESS),
                           socket.inet_aton(self.term_ip))
        sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
        print("[{}] Joined multicast group {}:{}".format(
            LABEL, MULTICAST_ADDRESS, MULTICAST_PORT
        ))

        while not stop_event.is_set():
            try:
                raw, addr = sock.recvfrom(8192)
            except socket.timeout:
                continue
            except Exception as e:
                print("[{}] recv error: {}".format(LABEL, e))
                continue

            recv_ts = time.time()

            if len(raw) < 4:
                continue

            magic_candidate = struct.unpack_from('!I', raw, 0)[0]

            if magic_candidate == REPAIR_MAGIC:
                try:
                    encoded_ids, coeffs, payload = decode_repair_wire(raw)
                except ValueError as e:
                    print("[{}] Bad repair packet: {}".format(LABEL, e))
                    continue

                stats.record_repair_recv(len(raw))

                key = frozenset(encoded_ids)
                with repair_lock:
                    repair_buf.setdefault(key, []).append((coeffs, payload, recv_ts))

                print("[{}] Repair  seqs={}".format(LABEL, encoded_ids))
                try_recover(encoded_ids, len(payload), recv_ts)

            else:
                try:
                    seq, payload = decode_data_wire(raw)
                except ValueError as e:
                    print("[{}] Bad data packet: {}".format(LABEL, e))
                    continue

                stats.record_recv(len(raw))

                with buf_lock:
                    is_new = seq not in recv_buf
                    if is_new:
                        recv_buf[seq] = payload
                        highest_seq = max(highest_seq, seq)
                        highest_wire_seq = max(highest_wire_seq, seq)

                if is_new:
                    text = payload.rstrip(b'\x00').decode('utf-8', errors='replace')
                    print("[{}] seq={:4d}  '{}'".format(LABEL, seq, text[:50]))


# ── NACK thread ────────────────────────────────────────────────────────────────

class NackThread(threading.Thread):
    def __init__(self, server_ip, term_ip):
        super(NackThread, self).__init__()
        self.daemon     = True
        self.server_ip  = server_ip
        self.nacked     = set()
        self.sock       = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind((term_ip, 0))

    def run(self):
        print("[{}] NACK thread started (interval={}s window={})".format(
            LABEL, NACK_INTERVAL, NACK_WINDOW
        ))
        while not stop_event.is_set():
            time.sleep(NACK_INTERVAL)

            with buf_lock:
                hi   = highest_wire_seq
                have = set(recv_buf.keys())

            if hi < 1:
                continue

            # Count expected = every seq from 0 to highest seen
            # (done here so it's updated continuously; the final value is what matters)
            stats.pkts_expected = hi + 1

            lo          = max(0, hi - NACK_WINDOW)
            missing     = [s for s in range(lo, hi) if s not in have]
            new_missing = [s for s in missing if s not in self.nacked]

            if not new_missing:
                continue

            print("[{}] Sending NACK for seqs {}".format(LABEL, new_missing))
            wire = struct.pack('!{}I'.format(len(new_missing)), *new_missing)
            try:
                self.sock.sendto(wire, (self.server_ip, NACK_PORT))
                stats.record_send(len(wire))
            except OSError as e:
                print("[{}] NACK send error: {}".format(LABEL, e))
            self.nacked.update(new_missing)


# ── Entry point ────────────────────────────────────────────────────────────────

def run_terminal(server_ip, term_ip):
    # type: (str, str) -> None
    signal.signal(signal.SIGTERM, shutdown)
    print("[{}] Starting (duration={}s)".format(LABEL, DURATION))

    RecvThread(term_ip).start()
    time.sleep(1)
    NackThread(server_ip, term_ip).start()

    stop_event.wait(DURATION)
    stop_event.set()

    # Final expected count: based only on wire-received seqs, not recovered ones,
    # so that loss_rate reflects actual OTA loss before repair.
    with buf_lock:
        if highest_wire_seq >= 0:
            stats.pkts_expected = highest_wire_seq + 1

    print("[{}] Duration elapsed, saving stats.".format(LABEL))
    stats.save(STATS_FILE)


if __name__ == '__main__':
    if len(sys.argv) < 3:
        print("Usage: python3 terminal32.py SERVER_IP TERMINAL_IP")
        sys.exit(1)
    run_terminal(sys.argv[1], sys.argv[2])
