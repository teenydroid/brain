"""Keccak-256, as Solana programs and Ethereum use it: the original Keccak
padding (0x01 ... 0x80), not SHA3-256's (0x06 ... 0x80), so
`hashlib.sha3_256` gives a different answer for every input. The standard
library has no Keccak, so this is the permutation written out (FIPS 202,
section 3, with rate 1088 bits and capacity 512).

    keccak256(b"").hex() == "c5d2460186f7233c927e7db2dcc703c0e500b653ca82273b7bfad8045d85a470"
"""

from __future__ import annotations

_MASK = (1 << 64) - 1
_RATE = 136  # bytes: 1600 - 2 * 256 bits

_ROUND_CONSTANTS = (
    0x0000000000000001, 0x0000000000008082, 0x800000000000808A, 0x8000000080008000,
    0x000000000000808B, 0x0000000080000001, 0x8000000080008081, 0x8000000000008009,
    0x000000000000008A, 0x0000000000000088, 0x0000000080008009, 0x000000008000000A,
    0x000000008000808B, 0x800000000000008B, 0x8000000000008089, 0x8000000000008003,
    0x8000000000008002, 0x8000000000000080, 0x000000000000800A, 0x800000008000000A,
    0x8000000080008081, 0x8000000000008080, 0x0000000080000001, 0x8000000080008008,
)

#: Rotation offsets r[x][y], indexed x + 5 * y.
_ROTATIONS = (
    0, 1, 62, 28, 27,
    36, 44, 6, 55, 20,
    3, 10, 43, 25, 39,
    41, 45, 15, 21, 8,
    18, 2, 61, 56, 14,
)


def _rotl(value: int, shift: int) -> int:
    return ((value << shift) | (value >> (64 - shift))) & _MASK if shift else value


def _permute(state: list[int]) -> None:
    """Keccak-f[1600] on 25 lanes, in place (lane (x, y) at x + 5 * y)."""
    for constant in _ROUND_CONSTANTS:
        # theta
        c = [state[x] ^ state[x + 5] ^ state[x + 10] ^ state[x + 15] ^ state[x + 20] for x in range(5)]
        d = [c[(x - 1) % 5] ^ _rotl(c[(x + 1) % 5], 1) for x in range(5)]
        for i in range(25):
            state[i] ^= d[i % 5]
        # rho and pi
        moved = [0] * 25
        for x in range(5):
            for y in range(5):
                moved[y + 5 * ((2 * x + 3 * y) % 5)] = _rotl(state[x + 5 * y], _ROTATIONS[x + 5 * y])
        # chi
        for y in range(5):
            row = moved[5 * y:5 * y + 5]
            for x in range(5):
                state[x + 5 * y] = row[x] ^ ((~row[(x + 1) % 5]) & row[(x + 2) % 5])
        # iota
        state[0] ^= constant


def keccak256(data: bytes) -> bytes:
    """The 32-byte Keccak-256 digest of `data`."""
    padded = bytearray(data)
    padded.append(0x01)
    while len(padded) % _RATE:
        padded.append(0)
    padded[-1] |= 0x80
    state = [0] * 25
    for start in range(0, len(padded), _RATE):
        block = padded[start:start + _RATE]
        for i in range(_RATE // 8):
            state[i] ^= int.from_bytes(block[8 * i:8 * i + 8], "little")
        _permute(state)
    return b"".join(lane.to_bytes(8, "little") for lane in state[:4])
