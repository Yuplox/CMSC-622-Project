"""
metrics.py — Lightweight stats collector for use case experiments.

Each process (server or terminal) creates a Stats object, increments counters
as it sends/receives packets, then calls save(path) at shutdown to write a
JSON file the experiment runner can read.

Tracked values
──────────────
  bytes_sent        total bytes sent over the wire (headers + payload)
  bytes_received    total bytes received over the wire
  pkts_sent         total DATA packets sent
  pkts_received     total DATA packets received
  pkts_expected     total packets the terminal expected to receive (seq-number-based)
  retransmissions   repair packets sent (server) / repair packets received (terminal)
  rtt_samples       list of one-way RTT samples in seconds (send-ts embedded in payload)

Derived at report time
──────────────────────
  loss_rate         = max(0, pkts_expected - pkts_received) / pkts_expected
  mean_rtt          = mean(rtt_samples)
"""

import json
import os
import threading
import time


class Stats(object):
    def __init__(self, role, label):
        # type: (str, str) -> None
        """
        role  : 'server' or 'terminal'
        label : human-readable name for the log file, e.g. 'server31', 'termA32'
        """
        self.role  = role
        self.label = label
        self._lock = threading.Lock()

        self.bytes_sent       = 0
        self.bytes_received   = 0
        self.pkts_sent        = 0
        self.pkts_received    = 0
        self.pkts_expected    = 0   # incremented by terminal based on seq gaps
        self.retransmissions  = 0   # repair pkts sent (server) or received (terminal)
        self.rtt_samples      = []  # list of floats (seconds)

    # ── Increment helpers (thread-safe) ───────────────────────────────────────

    def record_send(self, nbytes):
        # type: (int) -> None
        with self._lock:
            self.bytes_sent += nbytes
            self.pkts_sent  += 1

    def record_recv(self, nbytes):
        # type: (int) -> None
        with self._lock:
            self.bytes_received += nbytes
            self.pkts_received  += 1

    def record_repair_sent(self, nbytes):
        # type: (int) -> None
        with self._lock:
            self.bytes_sent      += nbytes
            # Intentionally NOT incrementing self.pkts_sent to separate data from repair
            self.retransmissions += 1

    def record_repair_recv(self, nbytes):
        # type: (int) -> None
        with self._lock:
            self.bytes_received  += nbytes
            # Intentionally NOT incrementing self.pkts_received to separate data from repair
            self.retransmissions += 1

    def record_expected(self, count=1):
        # type: (int) -> None
        with self._lock:
            self.pkts_expected += count

    def record_rtt(self, rtt_seconds):
        # type: (float) -> None
        with self._lock:
            self.rtt_samples.append(rtt_seconds)

    # ── Derived metrics ───────────────────────────────────────────────────────

    def loss_rate(self):
        # type: () -> float
        if self.pkts_expected == 0:
            return 0.0
        lost = max(0, self.pkts_expected - self.pkts_received)
        return lost / float(self.pkts_expected)

    def mean_rtt_ms(self):
        # type: () -> float
        if not self.rtt_samples:
            return 0.0
        return (sum(self.rtt_samples) / len(self.rtt_samples)) * 1000.0

    # ── Persistence ───────────────────────────────────────────────────────────

    def to_dict(self):
        # type: () -> dict
        return {
            'role':            self.role,
            'label':           self.label,
            'bytes_sent':      self.bytes_sent,
            'bytes_received':  self.bytes_received,
            'pkts_sent':       self.pkts_sent,
            'pkts_received':   self.pkts_received,
            'pkts_expected':   self.pkts_expected,
            'retransmissions': self.retransmissions,
            'loss_rate':       self.loss_rate(),
            'mean_rtt_ms':     self.mean_rtt_ms(),
            'rtt_sample_count': len(self.rtt_samples),
        }

    def save(self, path):
        # type: (str) -> None
        with open(path, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)
        print("[{}] Stats saved to {}".format(self.label, path))

    @staticmethod
    def load(path):
        # type: (str) -> dict
        with open(path) as f:
            return json.load(f)