"""Pure-Python Keccak-256 (used for EIP-55 Ethereum address checksums).

Note: this is Keccak-256 (original padding), *not* NIST SHA3-256.  Some
OpenSSL builds ship a non-conforming ``hashlib.sha3_256`` (it fails the FIPS-202
empty-string vector ``a7ffc6f8...``), so we compute Keccak ourselves -- the
permutation runs lazily on first checksum validation.
"""

from __future__ import annotations

from functools import lru_cache

_MASK = (1 << 64) - 1
_ROTC = [1, 3, 6, 10, 15, 21, 28, 36, 45, 55, 2, 14, 27, 41, 56, 8, 25, 43, 62, 18, 39, 61, 20, 44]
_PILN = [10, 7, 11, 17, 18, 3, 5, 16, 8, 21, 24, 4, 15, 23, 19, 13, 12, 2, 20, 14, 22, 9, 6, 1]
_RC = [
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
]


def _rol(x: int, n: int) -> int:
    n %= 64
    return ((x << n) | (x >> (64 - n))) & _MASK if n else x


def _keccak_f(state: list[int]) -> None:
    for rc in _RC:
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rol(c[(x + 1) % 5], 1) for x in range(5)]
        for x in range(5):
            for y in range(5):
                state[x + 5 * y] ^= d[x]
        x, y = 1, 0
        cur = state[x + 5 * y]
        for t in range(24):
            x, y = y, (2 * x + 3 * y) % 5
            nx = state[x + 5 * y]
            state[x + 5 * y] = _rol(cur, _ROTC[t])
            cur = nx
        for y in range(5):
            row = [state[x + 5 * y] for x in range(5)]
            for x in range(5):
                state[x + 5 * y] = row[x] ^ ((~row[(x + 1) % 5]) & row[(x + 2) % 5])
        state[0] ^= rc


@lru_cache(maxsize=512)
def keccak256(data: bytes) -> bytes:
    """Keccak-256 of ``data``."""
    rate = 136
    # Keccak padding (0x01 ... 0x80), NOT SHA3's 0x06.
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % rate != 0:
        padded.append(0)
    padded[-1] ^= 0x80
    state = [0] * 25
    for off in range(0, len(padded), rate):
        block = padded[off : off + rate]
        for i in range(rate // 8):
            state[i] ^= int.from_bytes(block[i * 8 : i * 8 + 8], "little")
        _keccak_f(state)
    out = b"".join(state[i].to_bytes(8, "little") for i in range(4))
    return out[:32]


def keccak256_hex(data: bytes) -> str:
    return keccak256(data).hex()
