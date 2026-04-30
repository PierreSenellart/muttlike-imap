"""Size-range parsing for the ~z modifier."""

from __future__ import annotations

import re

_SIZE_RE = re.compile(r"^(\d+)([KkMm]?)$")
_SIZE_PREFIX_RE = re.compile(r"(\d+[KkMm]?)")


def parse_size(s: str) -> int | None:
    """Parse N[KkMm] into a byte count."""
    m = _SIZE_RE.match(s)
    if not m:
        return None
    n, unit = int(m.group(1)), m.group(2).lower()
    if unit == "k":
        return n * 1024
    if unit == "m":
        return n * 1024 * 1024
    return n


def parse_size_range(s: str) -> str:
    """Parse a mutt-style size range (~z): <N, >N, N-M, -N, N-, N (plain).

    Returns an IMAP criteria string using SMALLER/LARGER. Raises ValueError on bad input.
    """
    s = s.strip()
    if not s:
        raise ValueError("~z requires a size range argument")
    if s[0] in "<>":
        n = parse_size(s[1:])
        if n is None:
            raise ValueError(f"invalid size: {s[1:]!r}")
        return f"SMALLER {n}" if s[0] == "<" else f"LARGER {n}"
    if s.startswith("-"):
        n = parse_size(s[1:])
        if n is None:
            raise ValueError(f"invalid size: {s[1:]!r}")
        return f"SMALLER {n + 1}"
    m = _SIZE_PREFIX_RE.match(s)
    if not m:
        raise ValueError(f"invalid size range: {s!r}")
    first = parse_size(m.group(1))
    assert first is not None
    rest = s[m.end() :]
    if not rest:
        return f"LARGER {max(first - 1, 0)} SMALLER {first + 1}"
    if rest == "-":
        return f"LARGER {max(first - 1, 0)}"
    if rest.startswith("-"):
        n2 = parse_size(rest[1:])
        if n2 is None:
            raise ValueError(f'invalid size after "-": {rest[1:]!r}')
        return f"LARGER {max(first - 1, 0)} SMALLER {n2 + 1}"
    raise ValueError(f"invalid size range: {s!r}")
