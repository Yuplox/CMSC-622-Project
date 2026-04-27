"""
experiment.py — Automated experiment runner for SatCom network coding evaluation.

Runs all 9 combinations of:
  Scenario  x  Use Case
  ────────────────────────────────────────────────────────────────────
  control       baseline link parameters (1% loss, 10 Mbps user link)
  jamming       high loss + reduced bandwidth (30% loss, 2 Mbps)
  ddos          attacker host floods server during the experiment
  ────────────────────────────────────────────────────────────────────
  control_nc    no network coding — terminals send/receive independently
  use_case_31   Use Case 3.1  Two-Way Relay (XOR coding)
  use_case_32   Use Case 3.2  Reliable Multicast (GF(2^8) linear coding)

For each combination it:
  1. Builds a fresh Mininet topology with the scenario's link parameters
  2. Launches the appropriate server + terminals (+ attacker for DDoS)
  3. Waits for DURATION seconds
  4. Reads JSON stats files written by each process
  5. Aggregates metrics across terminals

After all 9 runs it writes results.csv and prints a formatted table.

Usage
─────
  sudo python3 experiment.py [--duration SECONDS] [--out results.csv]
"""

import argparse
import csv
import json
import os
import sys
import time

from mininet.net import Mininet
from mininet.link import TCLink
from mininet.log import setLogLevel

from shared import SatelliteTopo

# ── Experiment parameters ──────────────────────────────────────────────────────

# How long each individual experiment runs (seconds).
# Longer = more stable averages; shorter = faster overall run.
DEFAULT_DURATION = 30

# Mininet topology base parameters (match existing run.py)
BASE_BW_USER   = 10     # Mbps  terminal <-> satellite
BASE_BW_FEED   = 100    # Mbps  satellite <-> gateway
BASE_DELAY     = '250ms'
BASE_LOSS      = 1      # %

# Jamming scenario overrides
JAM_BW_USER    = 2      # Mbps  — heavily degraded user link
JAM_LOSS       = 30     # %     — simulates severe RF interference

# DDoS: attacker sends this many packets/second at the server
DDOS_FLOOD_RATE = 500

# Where each process writes its stats JSON
STATS_DIR = '/tmp/satcom_stats'

# ── Scenario definitions ────────────────────────────────────────────────────────

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

USE_CASES = [
    {'name': 'control_nc', 'label': 'No Coding (control)'},
    {'name': 'use_case_31', 'label': 'Use Case 3.1 (XOR relay)'},
    {'name': 'use_case_32', 'label': 'Use Case 3.2 (GF multicast)'},
]

# ── Helpers ────────────────────────────────────────────────────────────────────

def ensure_stats_dir():
    if not os.path.exists(STATS_DIR):
        os.makedirs(STATS_DIR)


def stats_path(scenario_name, use_case_name, role):
    # type: (str, str, str) -> str
    fname = '{}_{}_{}.json'.format(scenario_name, use_case_name, role)
    return os.path.join(STATS_DIR, fname)


def load_stats(path):
    # type: (str) -> dict
    if not os.path.exists(path):
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def env_for(scenario_name, use_case_name, role, duration, extra=None):
    # type: (str, str, str, int, dict) -> dict
    """Build the env dict passed to a Mininet host command."""
    sf = stats_path(scenario_name, use_case_name, role)
    e = {
        'STATS_FILE': sf,
        'DURATION':   str(duration),
        'PYTHONPATH': os.getcwd(),
    }
    if extra:
        e.update(extra)
    return e


def env_str(env_dict):
    # type: (dict) -> str
    """Convert env dict to a shell export prefix string."""
    parts = []
    for k, v in env_dict.items():
        parts.append('{}="{}"'.format(k, v))
    return ' '.join(parts)


def wait_for_files(paths, timeout=5):
    # type: (list, int) -> None
    """Poll until all stats files exist or timeout."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if all(os.path.exists(p) for p in paths):
            return
        time.sleep(0.5)


# ── No-coding control: plain unicast terminal (no server) ─────────────────────

def run_control_nc(net, scenario, duration):
    # type: (object, dict, int) -> dict
    """
    Baseline with no network coding.
    TermA and TermB each independently send UDP packets to each other
    via the server (which just echoes back — no XOR).
    We measure raw bytes on the wire.
    """
    sname = scenario['name']
    server = net.get('ser0')
    termA  = net.get('term0')
    termB  = net.get('term1')

    server_ip = server.IP()
    termA_ip  = termA.IP()
    termB_ip  = termB.IP()

    # Use a trivial echo server
    echo_stats = stats_path(sname, 'control_nc', 'server')
    term_a_stats = stats_path(sname, 'control_nc', 'termA')
    term_b_stats = stats_path(sname, 'control_nc', 'termB')

    server_env = env_str({
        'STATS_FILE': echo_stats,
        'DURATION':   str(duration),
        'PYTHONPATH': '.',
    })
    termA_env = env_str({
        'STATS_FILE': term_a_stats,
        'DURATION':   str(duration),
        'LABEL':      'termA_nc',
        'PYTHONPATH': '.',
    })
    termB_env = env_str({
        'STATS_FILE': term_b_stats,
        'DURATION':   str(duration),
        'LABEL':      'termB_nc',
        'PYTHONPATH': '.',
    })

    server.cmd('{} python3 -u server_nc.py {} > /tmp/nc_server.log 2>&1 &'.format(
        server_env, server_ip))
    time.sleep(0.5)
    termA.cmd(('{} python3 -u terminal_nc.py {} {} "Hello from A" '
               '> /tmp/nc_termA.log 2>&1 &').format(termA_env, server_ip, termA_ip))
    termB.cmd(('{} python3 -u terminal_nc.py {} {} "Hello from B" '
               '> /tmp/nc_termB.log 2>&1 &').format(termB_env, server_ip, termB_ip))

    time.sleep(duration + 3)
    wait_for_files([term_a_stats, term_b_stats])

    sA = load_stats(term_a_stats)
    sB = load_stats(term_b_stats)
    ss = load_stats(echo_stats)
    return aggregate(ss, [sA, sB])


def run_use_case_31(net, scenario, duration):
    # type: (object, dict, int) -> dict
    sname = scenario['name']
    server = net.get('ser0')
    termA  = net.get('term0')
    termB  = net.get('term1')

    server_ip = server.IP()
    termA_ip  = termA.IP()
    termB_ip  = termB.IP()

    srv_stats  = stats_path(sname, 'use_case_31', 'server')
    termA_stats = stats_path(sname, 'use_case_31', 'termA')
    termB_stats = stats_path(sname, 'use_case_31', 'termB')

    server_env = env_str({'STATS_FILE': srv_stats,   'DURATION': str(duration), 'PYTHONPATH': '.'})
    termA_env  = env_str({'STATS_FILE': termA_stats, 'DURATION': str(duration),
                          'LABEL': 'termA31', 'PYTHONPATH': '.'})
    termB_env  = env_str({'STATS_FILE': termB_stats, 'DURATION': str(duration),
                          'LABEL': 'termB31', 'PYTHONPATH': '.'})

    server.cmd('{} python3 -u server31.py {} > /tmp/31_server.log 2>&1 &'.format(
        server_env, server_ip))
    time.sleep(0.5)
    termA.cmd(('{} python3 -u terminal31.py {} {} "Hello from A" '
               '> /tmp/31_termA.log 2>&1 &').format(termA_env, server_ip, termA_ip))
    termB.cmd(('{} python3 -u terminal31.py {} {} "Hello from B" '
               '> /tmp/31_termB.log 2>&1 &').format(termB_env, server_ip, termB_ip))

    time.sleep(duration + 3)
    wait_for_files([termA_stats, termB_stats])

    sA = load_stats(termA_stats)
    sB = load_stats(termB_stats)
    ss = load_stats(srv_stats)
    return aggregate(ss, [sA, sB])


def run_use_case_32(net, scenario, duration):
    # type: (object, dict, int) -> dict
    sname = scenario['name']
    server = net.get('ser0')

    server_ip = server.IP()

    srv_stats = stats_path(sname, 'use_case_32', 'server')
    server_env = env_str({'STATS_FILE': srv_stats, 'DURATION': str(duration), 'PYTHONPATH': '.'})

    server.cmd('{} python3 -u server32.py {} > /tmp/32_server.log 2>&1 &'.format(
        server_env, server_ip))
    time.sleep(0.5)

    TERM_COUNT = 10
    term_stats_paths = []
    for i in range(TERM_COUNT):
        term = net.get('term{}'.format(i))
        term_ip = term.IP()
        label = 'term{}_32'.format(i)
        tstat = stats_path(sname, 'use_case_32', label)
        term_stats_paths.append(tstat)
        term_env = env_str({'STATS_FILE': tstat, 'DURATION': str(duration),
                            'LABEL': label, 'PYTHONPATH': '.'})
        term.cmd('{} python3 -u terminal32.py {} {} > /tmp/32_term{}.log 2>&1 &'.format(
            term_env, server_ip, term_ip, i))

    time.sleep(duration + 3)
    wait_for_files(term_stats_paths)

    terminal_stats = [load_stats(p) for p in term_stats_paths]
    ss = load_stats(srv_stats)
    return aggregate(ss, terminal_stats)


def inject_ddos(net, server_ip, scenario, duration):
    # type: (object, str, dict, int) -> None
    """Launch attacker from a dedicated host if DDoS scenario."""
    attacker = net.get('atk0')
    atk_env = env_str({
        'DURATION':   str(duration),
        'FLOOD_RATE': str(DDOS_FLOOD_RATE),
        'LABEL':      'attacker',
        'PYTHONPATH': '.',
    })
    attacker.cmd('{} python3 -u attacker.py {} > /tmp/attacker.log 2>&1 &'.format(
        atk_env, server_ip))


# ── Aggregate stats across server + terminals ──────────────────────────────────

def aggregate(server_stats, terminal_stats_list):
    # type: (dict, list) -> dict
    """
    Combine stats dicts from server and all terminals into one result row.

    bytes_sent      = total bytes on the wire (server sent + terminals sent)
    retransmissions = repair packets sent by server (or repair rounds for 3.1)
    mean_rtt_ms     = average across all terminals that have RTT samples
    loss_rate       = average across terminals
    """
    total_bytes  = server_stats.get('bytes_sent', 0)
    total_rtx    = server_stats.get('retransmissions', 0)
    rtt_samples  = []
    loss_rates   = []
    pkts_recv    = 0
    pkts_exp     = 0

    for ts in terminal_stats_list:
        if not ts:
            continue
        total_bytes += ts.get('bytes_sent', 0)
        total_rtx   += ts.get('retransmissions', 0)
        # pkts_received in metrics counts both data and repair packets; subtract
        # retransmissions (repair packets received) so this column only reflects
        # data packets and is directly comparable to pkts_expected.
        data_pkts_recv = ts.get('pkts_received', 0) - ts.get('retransmissions', 0)
        pkts_recv   += max(0, data_pkts_recv)
        pkts_exp    += ts.get('pkts_expected', 0)
        mean_rtt_ms = ts.get('mean_rtt_ms', 0.0)
        if ts.get('rtt_sample_count', 0) > 0:
            rtt_samples.append(mean_rtt_ms)
        loss_rates.append(ts.get('loss_rate', 0.0))

    mean_rtt  = sum(rtt_samples) / len(rtt_samples) if rtt_samples else 0.0
    mean_loss = sum(loss_rates)  / len(loss_rates)  if loss_rates  else 0.0

    return {
        'bytes_sent':      total_bytes,
        'retransmissions': total_rtx,
        'mean_rtt_ms':     mean_rtt,
        'loss_rate':       mean_loss,
        'pkts_received':   pkts_recv,
        'pkts_expected':   pkts_exp,
    }


# ── Topology builder (adds optional attacker host) ─────────────────────────────

class SatelliteTopoWithAttacker(SatelliteTopo):
    """Extends SatelliteTopo to optionally add an attacker host."""

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
            print("        Building topology...")

            uc_name = use_case['name']
            term_count = 10 if uc_name == 'use_case_32' else 2
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
                inject_ddos(net, server_ip, scenario, duration)
                time.sleep(0.5)

            # Launch the use case
            uc_name = use_case['name']
            if uc_name == 'control_nc':
                metrics = run_control_nc(net, scenario, duration)
            elif uc_name == 'use_case_31':
                metrics = run_use_case_31(net, scenario, duration)
            elif uc_name == 'use_case_32':
                metrics = run_use_case_32(net, scenario, duration)
            else:
                metrics = {}

            net.stop()
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


# ── CSV + table output ─────────────────────────────────────────────────────────

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
    # type: (list, str) -> None
    fieldnames = [c[0] for c in COLUMNS]
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for row in results:
            writer.writerow({k: row.get(k, '') for k in fieldnames})
    print("\nResults saved to {}".format(path))


def print_table(results):
    # type: (list) -> None
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


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description='SatCom network coding experiments')
    parser.add_argument('--duration', type=int, default=DEFAULT_DURATION,
                        help='Seconds per experiment run (default {})'.format(DEFAULT_DURATION))
    parser.add_argument('--out', default='results.csv',
                        help='Output CSV path (default results.csv)')
    args = parser.parse_args()

    if os.geteuid() != 0:
        print("ERROR: experiment.py must be run as root (sudo).")
        sys.exit(1)

    print("Starting experiments: {} scenarios x {} use cases = {} runs".format(
        len(SCENARIOS), len(USE_CASES), len(SCENARIOS) * len(USE_CASES)
    ))
    print("Duration per run: {}s  |  Estimated total: ~{}min".format(
        args.duration,
        round(len(SCENARIOS) * len(USE_CASES) * (args.duration + 10) / 60, 1)
    ))

    results = run_experiment(args.duration)
    write_csv(results, args.out)
    print_table(results)


if __name__ == '__main__':
    main()
