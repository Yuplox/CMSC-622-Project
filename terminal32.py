import os
import signal
import socket
import struct
import sys
import threading
import time

from shared import *
from gf256 import gf_solve, gf_scale, gf_add_packets
from metrics import Stats


STATS_FILE = None

# ── Shared state ───────────────────────────────────────────────────────────────
buf_lock    = threading.Lock()
recv_buf    = OrderedDict()
repair_lock = threading.Lock()
repair_buf  = {}
highest_seq      = -1   # highest seq ever placed in recv_buf (including recovered)
highest_wire_seq = -1   # highest seq received directly over the air (never recovered)

stats = Stats('terminal', LABEL)


# Ensure STATS_FILE is saved even when terminated early
def shutdown(signum, frame):
    if STATS_FILE is not None:
        stats.save(STATS_FILE)
    sys.exit(0)




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


def try_recover(encoded_ids, pkt_len):

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


    with buf_lock:
        for sid, pdata in zip(missing, recovered):
            recv_buf[sid] = pdata
            highest_seq = max(highest_seq, sid)  # Track highest seq even if recovered
            text = pdata.rstrip(b'\x00').decode('utf-8', errors='replace')
            print("  [{}] RECOVERED seq={}: '{}'".format(LABEL, sid, text[:50]))



def run_terminal(server_ip, term_ip, term_id, label="term32"):
    signal.signal(signal.SIGTERM, shutdown)

    print(f"[{label}] Starting (duration={DURATION}s)")
    
    # Create listen socket and join multicast group
    listen_socket = setup_socket('', MULTICAST_PORT)
    setup_multicast_client(listen_socket, term_ip, MULTICAST_IP)

    # Create another socket to send NACKs from
    send_socket = setup_socket(term_ip, CLIENT_PORT)

    deadline = time.time() + DURATION
    highest_seq = -1
    while time.time() < deadline:
        
        # Check for any packets broadcasted
        while(True):
            try:
                packet, addr = listen_socket.recvfrom(BUFF_SIZE)

                # Extract header info and payload
                msg_type, seq_num, packet = TerminalProtocol.unpack_data(packet)
                

                if msg_type == MSG_DATA:
                    lat, lon, timestamp = GPSPayload.unpack_data(packet)

                    # Calculate stats
                    rtt = time.time() - timestamp
                    stats.record_recv(len(packet))
                    stats.record_rtt(rtt)

                elif msg_type == MSG_CODED:
                    pass
                    # TODO


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
        print("Usage: python3 terminal32.py SERVER_IP TERMINAL_IP TERMINAL_ID")
        sys.exit(1)

    # Check for optional stats file
    if len(sys.argv) == 5:
        STATS_FILE = sys.argv[4]

    time.sleep(0.1) # Wait 100ms for server to initialize
    run_terminal(sys.argv[1], sys.argv[2], sys.argv[3])