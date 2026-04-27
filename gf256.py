"""
gf256.py — Arithmetic in GF(2^8) using the AES irreducible polynomial
           x^8 + x^4 + x^3 + x + 1  (0x11b).

All elements are plain Python ints in [0, 255].
Addition  : XOR  (gf_add)
Multiply  : log/antilog table lookup  (gf_mul)
Inverse   : log/antilog  (gf_inv)

Also provides:
  gf_encode(packets, coeffs)  -> bytes  (linear combination)
  gf_solve(matrix, rhs)       -> list[bytes]  (Gaussian elimination, one unknown per row)
"""

from typing import List

# ── Build log / antilog tables ────────────────────────────────────────────────
POLY = 0x11b          # AES reduction polynomial
_EXP = [0] * 512     # antilog: _EXP[i] = g^i,  g = 0x03
_LOG = [0] * 256     # log:     _LOG[x] = i  s.t. g^i = x

x = 1
for i in range(255):
    _EXP[i] = x
    _LOG[x] = i
    # Multiply by generator g=0x03: x*0x03 = x*2 XOR x
    x2 = (x << 1) ^ POLY if (x << 1) & 0x100 else (x << 1)
    x2 &= 0xFF
    x = x2 ^ x

# Duplicate for wrap-around multiplication convenience
for i in range(255, 512):
    _EXP[i] = _EXP[i - 255]


def gf_add(a: int, b: int) -> int:
    """Addition in GF(2^8) is XOR."""
    return a ^ b


def gf_mul(a: int, b: int) -> int:
    """Multiplication in GF(2^8) via log/antilog tables."""
    if a == 0 or b == 0:
        return 0
    return _EXP[_LOG[a] + _LOG[b]]


def gf_inv(a: int) -> int:
    """Multiplicative inverse in GF(2^8).  a must be non-zero."""
    if a == 0:
        raise ZeroDivisionError("No inverse for 0 in GF(2^8)")
    return _EXP[255 - _LOG[a]]


def gf_div(a: int, b: int) -> int:
    return gf_mul(a, gf_inv(b))


# ── Packet-level operations ───────────────────────────────────────────────────

def gf_scale(data: bytes, c: int) -> bytes:
    """Multiply every byte of data by scalar c in GF(2^8)."""
    if c == 1:
        return data
    if c == 0:
        return bytes(len(data))
    return bytes(gf_mul(b, c) for b in data)


def gf_add_packets(a: bytes, b: bytes) -> bytes:
    """Element-wise GF add (XOR) two equal-length byte strings."""
    return bytes(x ^ y for x, y in zip(a, b))


def gf_encode(packets: dict, coeffs: dict) -> bytes:
    """
    Compute a single linear combination repair packet.

    packets : {packet_id -> bytes}  (all same length, padded)
    coeffs  : {packet_id -> int}    GF(2^8) coefficient, one per packet

    Returns the repair payload (same length as each input packet).
    """
    length = len(next(iter(packets.values())))
    repair = bytearray(length)
    for pid, data in packets.items():
        c = coeffs[pid]
        if c == 0:
            continue
        scaled = gf_scale(data, c)
        for i in range(length):
            repair[i] ^= scaled[i]
    return bytes(repair)


def gf_solve(coeff_matrix: List[List[int]],
             repair_packets: List[bytes],
             pkt_len: int) -> List[bytes]:
    """
    Solve  A · x = b  over GF(2^8) via Gaussian elimination,
    where each 'variable' x[j] is a full packet (bytes), not a scalar.

    coeff_matrix : R×C  list-of-lists of GF(2^8) ints
                   R = number of repair packets received
                   C = number of unknown packets to recover
    repair_packets : list of R byte-strings (the RHS vectors b)
    pkt_len        : byte-length of every packet

    Returns a list of C recovered byte-strings (the solution x).

    Raises ValueError if the system is under-determined or inconsistent.
    """
    R = len(coeff_matrix)
    C = len(coeff_matrix[0]) if R else 0

    if R < C:
        raise ValueError(f"Under-determined system: {R} equations, {C} unknowns.")

    # Work on mutable copies; augment matrix with RHS
    mat = [list(coeff_matrix[r]) for r in range(R)]
    rhs = [bytearray(repair_packets[r]) for r in range(R)]

    # Forward elimination (partial pivoting)
    pivot_row = 0
    for col in range(C):
        # Find a non-zero pivot in this column at or below pivot_row
        pivot = None
        for row in range(pivot_row, R):
            if mat[row][col] != 0:
                pivot = row
                break
        if pivot is None:
            raise ValueError(f"Singular matrix: no pivot in column {col}.")

        # Swap pivot row into place
        mat[pivot_row], mat[pivot] = mat[pivot], mat[pivot_row]
        rhs[pivot_row], rhs[pivot] = rhs[pivot], rhs[pivot_row]

        # Scale pivot row so the leading coefficient becomes 1
        scale = gf_inv(mat[pivot_row][col])
        mat[pivot_row] = [gf_mul(v, scale) for v in mat[pivot_row]]
        rhs[pivot_row] = bytearray(gf_mul(b, scale) for b in rhs[pivot_row])

        # Eliminate this column in all other rows
        for row in range(R):
            if row == pivot_row or mat[row][col] == 0:
                continue
            factor = mat[row][col]
            mat[row] = [gf_add(mat[row][j], gf_mul(factor, mat[pivot_row][j]))
                        for j in range(C)]
            scaled_pivot_rhs = gf_scale(bytes(rhs[pivot_row]), factor)
            rhs[row] = bytearray(gf_add_packets(bytes(rhs[row]), scaled_pivot_rhs))

        pivot_row += 1

    # After full RREF the first C rows directly give the solution
    return [bytes(rhs[c]) for c in range(C)]
