"""ABA routing transit numbers.

A routing number is nine digits d1..d9. The ninth is a check digit chosen so that

    3*(d1 + d4 + d7) + 7*(d2 + d5 + d8) + 1*(d3 + d6 + d9)  ≡ 0  (mod 10)

In a NACHA Entry Detail record the RDFI routing number is split: positions
4-11 carry the first eight digits (the "receiving DFI identification") and
position 12 carries the check digit. The batch/file *entry hash* is built from
the eight-digit part only.
"""

WEIGHTS = (3, 7, 1, 3, 7, 1, 3, 7, 1)


def check_digit(first_eight: str) -> int:
    """Return the check digit that completes an eight-digit routing prefix."""
    if len(first_eight) != 8 or not first_eight.isdigit():
        raise ValueError(f"routing prefix must be 8 digits, got {first_eight!r}")
    s = sum(int(d) * w for d, w in zip(first_eight, WEIGHTS[:8]))
    return (10 - s % 10) % 10


def is_valid(routing: str) -> bool:
    """True if `routing` is nine digits with a correct check digit.

    The leading two digits must also fall in a range the Fed actually assigns:
    00 (US government), 01-12 (Federal Reserve districts), 21-32 (thrifts),
    61-72 (electronic), 80 (traveler's cheques).
    """
    if len(routing) != 9 or not routing.isdigit():
        return False
    prefix = int(routing[:2])
    if not (prefix <= 12 or 21 <= prefix <= 32 or 61 <= prefix <= 72 or prefix == 80):
        return False
    return sum(int(d) * w for d, w in zip(routing, WEIGHTS)) % 10 == 0


def split(routing: str) -> tuple[str, str]:
    """Split a nine-digit routing number into (eight-digit DFI id, check digit)."""
    if not is_valid(routing):
        raise ValueError(f"invalid ABA routing number {routing!r}")
    return routing[:8], routing[8]
