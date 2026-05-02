import csv
import json
import os
import sys
import time

from mininet.net import Mininet
from mininet.link import TCLink
from mininet.log import setLogLevel
from mininet.clean import cleanup

from shared import *


# Jamming scenario overrides
JAM_BW_USER = 2
JAM_LOSS = 30

# DDoS: attacker sends this many packets/second at the server
DDOS_FLOOD_RATE = 5000

# Where each process writes its stats JSON
STATS_DIR = '/tmp/satcom_stats'

# All scenarios
SCENARIOS = [
    {
        'name':    'control',
        'label':   'Control',
        'bw_user': BASE_BW_USER,
        'bw_feed': BASE_BW_FEED,
        'delay':   BASE_DELAY,
        'loss':    BASE_LOSS,
        'ddos':    False,
    },
    {
        'name':    'jamming',
        'label':   'Jamming',
        'bw_user': JAM_BW_USER,
        'bw_feed': BASE_BW_FEED,
        'delay':   BASE_DELAY,
        'loss':    JAM_LOSS,
        'ddos':    False,
    },
    {
        'name':    'ddos',
        'label':   'DDoS',
        'bw_user': BASE_BW_USER,
        'bw_feed': BASE_BW_FEED,
        'delay':   BASE_DELAY,
        'loss':    BASE_LOSS,
        'ddos':    True,
    },
]

# All use cases
USE_CASES = [
    {'name': 'control_nc', 'label': 'No Coding (control)'},
    {'name': 'use_case_31', 'label': 'Use Case 3.1 (XOR relay)'},
    {'name': 'use_case_32', 'label': 'Use Case 3.2 (GF multicast)'},
]


# Create stats folder if it does not exist
def ensure_stats_dir():
    if not os.path.exists(STATS_DIR):
        os.makedirs(STATS_DIR)
    else:
        # Delete old stats to prevent cross-contamination
        for f in os.listdir(STATS_DIR):
            if f.endswith('.json'):
                os.remove(os.path.join(STATS_DIR, f))


# Create a path to stats file
def stats_path(scenario_name, use_case_name, role):
    fname = f"{scenario_name}_{use_case_name}_{role}.json"
    return os.path.join(STATS_DIR, fname)


# Load json from stats file at path
def load_stats(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}



def wait_for_files(paths, timeout=5):
    # type: (list, int) -> None
    """Poll until all stats files exist or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(os.path.exists(p) for p in paths):
            return
        time.sleep(0.5)


# No-coding
def run_control_nc(net, scenario):
    # Get scenario name
    sname = scenario['name']

    # Get info on terminals
    termA  = net.get('term0')
    termB  = net.get('term1')
    termA_ip  = termA.IP()
    termB_ip  = termB.IP()

    # Create stat paths
    term_a_stats = stats_path(sname, 'control_nc', 'termA')
    term_b_stats = stats_path(sname, 'control_nc', 'termB')

    # Run simulations
    termA.cmd(f'python3 -u terminal_nc.py {termA_ip} {termB_ip} 0 {term_a_stats}> {sname}_term_nc_A.log 2>&1 &')
    termB.cmd(f'python3 -u terminal_nc.py {termB_ip} {termA_ip} 1 {term_b_stats}> {sname}_term_nc_B.log 2>&1 &')

    # Wait for them to complete
    time.sleep(DURATION + 3)
    wait_for_files([term_a_stats, term_b_stats])

    # Collect stats
    sA = load_stats(term_a_stats)
    sB = load_stats(term_b_stats)
    return aggregate(None, [sA, sB])


# Use case 3.1
def run_use_case_31(net, scenario):
    # Get scenario name
    sname = scenario['name']

    # Get info on terminals
    server = net.get('ser0')
    termA  = net.get('term0')
    termB  = net.get('term1')
    server_ip = server.IP()
    termA_ip  = termA.IP()
    termB_ip  = termB.IP()

    # Create stat paths
    srv_stats  = stats_path(sname, 'use_case_31', 'server')
    termA_stats = stats_path(sname, 'use_case_31', 'termA')
    termB_stats = stats_path(sname, 'use_case_31', 'termB')

    # Run simulations
    server.cmd(f'python3 -u server31.py {server_ip} {termA_ip}:0 {termB_ip}:1 {srv_stats} > {sname}_server31.log 2>&1 &')
    termA.cmd(f'python3 -u terminal31.py {server_ip} {termA_ip} 0 {termA_stats} > {sname}_term31_A.log 2>&1 &')
    termB.cmd(f'python3 -u terminal31.py {server_ip} {termB_ip} 1 {termA_stats} > {sname}_term31_B.log 2>&1 &')

    # Wait for them to complete
    time.sleep(DURATION + 3)
    wait_for_files([termA_stats, termB_stats])

    # Collect stats
    sA = load_stats(termA_stats)
    sB = load_stats(termB_stats)
    ss = load_stats(srv_stats)
    return aggregate(ss, [sA, sB])


def run_use_case_32(net, scenario):
    sname = scenario['name']

    # Get info on server
    server = net.get('ser0')
    server_ip = server.IP()

    # Create stat paths
    srv_stats = stats_path(sname, 'use_case_32', 'server')
    term_stats_paths = []
    for i in range(TERMINAL_COUNT):
        term_stats_paths.append(stats_path(sname, 'use_case_32', f'term_{i}'))

    # Run simulations
    terminals = []
    for i in range(TERMINAL_COUNT):
        terminals.append(net.get(f'term{i}'))
        terminals[i].cmd(
            f'python3 -u terminal32.py {server_ip} {terminals[i].IP()} {i} {term_stats_paths[i]}> {sname}_term32-{i}.log 2>&1 &'
        )
    server.cmd(f'python3 -u server32.py {server_ip} {srv_stats}> {sname}_server32.log 2>&1 &')

    # Wait for them to complete
    time.sleep(DURATION + 3)
    wait_for_files(term_stats_paths)

    # Collect stats
    terminal_stats = [load_stats(p) for p in term_stats_paths]
    ss = load_stats(srv_stats)
    return aggregate(ss, terminal_stats)


def inject_ddos(net, server_ip, scenario):
    sname = scenario['name']
    attacker = net.get('atk0')
    attacker.cmd(f'python3 -u attacker.py {attacker.IP()} {server_ip} > {sname}_attacker.log 2>&1 &')


# ── Aggregate stats across server + terminals ──────────────────────────────────

def aggregate(server_stats, terminal_stats_list):
    if server_stats is not None:
        total_bytes = server_stats.get('bytes_sent', 0)
        total_rtx = server_stats.get('retransmissions', 0)
    else:
        total_bytes = 0
        total_rtx = 0
    
    rtt_samples = []
    pkts_recv = 0
    pkts_exp = 0

    for ts in terminal_stats_list:
        if not ts:
            continue
        total_bytes += ts.get('bytes_sent', 0)
        total_rtx   += ts.get('retransmissions', 0)
        
        # Extract purely data packets
        data_pkts_recv = ts.get('pkts_received', 0)
        pkts_recv   += max(0, data_pkts_recv)
        pkts_exp    += ts.get('pkts_expected', 0)
        
        mean_rtt_ms = ts.get('mean_rtt_ms', 0.0)
        if ts.get('rtt_sample_count', 0) > 0:
            rtt_samples.append(mean_rtt_ms)

    mean_rtt = sum(rtt_samples) / len(rtt_samples) if rtt_samples else 0.0

    # Calculate true global loss rate
    if pkts_exp > 0:
        lost = max(0, pkts_exp - pkts_recv)
        global_loss_rate = lost / float(pkts_exp)
    else:
        # If expected is 0, it means no terminal received a single packet 
        # to even establish a sequence number. This is total blackout.
        global_loss_rate = 1.0

    return {
        'bytes_sent':      total_bytes,
        'retransmissions': total_rtx,
        'mean_rtt_ms':     mean_rtt,
        'loss_rate':       global_loss_rate,
        'pkts_received':   pkts_recv,
        'pkts_expected':   pkts_exp,
    }

# ── Topology builder (adds optional attacker host) ─────────────────────────────

class SatelliteTopoWithAttacker(SatelliteTopo):
    def build(self, bandwidth, feedBandwidth, delay, loss, termCount=2, with_attacker=False):
        super(SatelliteTopoWithAttacker, self).build(
            bandwidth, feedBandwidth, delay, loss, termCount
        )
        if with_attacker:
            attacker  = self.addHost('atk0')
            satellite = 'sat0'
            # Attacker connects via satellite with same user-link params
            self.addLink(attacker, satellite,
                         bw=bandwidth, delay=delay, loss=0, use_htb=True)


# ── Main experiment loop ───────────────────────────────────────────────────────

def run_experiment(duration):
    # type: (int) -> list
    ensure_stats_dir()
    setLogLevel('warning')   # suppress Mininet info spam

    results = []

    total_runs = len(SCENARIOS) * len(USE_CASES)
    run_num = 0

    for scenario in SCENARIOS:
        for use_case in USE_CASES:
            run_num += 1
            print("\n[{}/{}] Scenario='{}' UseCase='{}'".format(
                run_num, total_runs, scenario['label'], use_case['label']
            ))
            print("Building topology...")

            uc_name = use_case['name']
            term_count = TERMINAL_COUNT if uc_name == 'use_case_32' else 2
            topo = SatelliteTopoWithAttacker(
                bandwidth     = scenario['bw_user'],
                feedBandwidth = scenario['bw_feed'],
                delay         = scenario['delay'],
                loss          = scenario['loss'],
                termCount     = term_count,
                with_attacker = scenario['ddos'],
            )
            net = Mininet(topo=topo, link=TCLink)
            net.start()

            server_ip = net.get('ser0').IP()

            # Launch DDoS attacker first so it's already flooding when server starts
            if scenario['ddos']:
                inject_ddos(net, server_ip, scenario)
                time.sleep(0.5)

            # Launch the use case
            uc_name = use_case['name']
            if uc_name == 'control_nc':
                metrics = run_control_nc(net, scenario)
            elif uc_name == 'use_case_31':
                metrics = run_use_case_31(net, scenario)
            elif uc_name == 'use_case_32':
                metrics = run_use_case_32(net, scenario)
            else:
                metrics = {}

            # Clean up old interfaces
            net.stop()
            cleanup()

            # Brief pause between runs so ports fully close
            time.sleep(2)

            row = {
                'scenario':        scenario['label'],
                'use_case':        use_case['label'],
                'bytes_sent':      metrics.get('bytes_sent', 0),
                'retransmissions': metrics.get('retransmissions', 0),
                'mean_rtt_ms':     round(metrics.get('mean_rtt_ms', 0.0), 2),
                'loss_rate_pct':   round(metrics.get('loss_rate', 0.0) * 100, 2),
                'pkts_received':   metrics.get('pkts_received', 0),
                'pkts_expected':   metrics.get('pkts_expected', 0),
            }
            results.append(row)
            print("        Done: bytes={} retx={} rtt={:.1f}ms loss={:.1f}%".format(
                row['bytes_sent'], row['retransmissions'],
                row['mean_rtt_ms'], row['loss_rate_pct']
            ))

    return results


# CSV Columns
COLUMNS = [
    ('scenario',        'Scenario',        16),
    ('use_case',        'Use Case',        28),
    ('bytes_sent',      'Bytes On Wire',   16),
    ('retransmissions', 'Retransmissions', 16),
    ('mean_rtt_ms',     'Mean RTT (ms)',   14),
    ('loss_rate_pct',   'Loss Rate (%)',   14),
    ('pkts_received',   'Pkts Received',   14),
    ('pkts_expected',   'Pkts Expected',   14),
]


def write_csv(results, path):
    fieldnames = [c[0] for c in COLUMNS]
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow({k: row.get(k, '') for k in fieldnames})
    print(f"\nResults saved to {path}")


def print_table(results):
    # Build header
    header = '  '.join(label.ljust(width) for _, label, width in COLUMNS)
    sep    = '  '.join('-' * width       for _, _,     width in COLUMNS)

    print('\n' + '=' * len(sep))
    print('SATCOM NETWORK CODING EXPERIMENT RESULTS')
    print('=' * len(sep))
    print(header)
    print(sep)

    last_scenario = None
    for row in results:
        # Print a blank line between scenario groups for readability
        if last_scenario and row['scenario'] != last_scenario:
            print()
        last_scenario = row['scenario']

        line_parts = []
        for key, _, width in COLUMNS:
            val = row.get(key, '')
            line_parts.append(str(val).ljust(width))
        print('  '.join(line_parts))

    print('=' * len(sep))


def main():
    if os.geteuid() != 0:
        print("ERROR: experiment.py must be run as root (sudo).")
        sys.exit(1)

    print(f"Starting experiments: {len(SCENARIOS)} scenarios x {len(USE_CASES)} use cases = {len(SCENARIOS) * len(USE_CASES)} runs")
    print(f"Duration per run: {DURATION}s")

    results = run_experiment(DURATION)
    write_csv(results, "experiment.csv")
    print_table(results)


if __name__ == '__main__':
    main()
