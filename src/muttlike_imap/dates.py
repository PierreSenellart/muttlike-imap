"""Date-range parsing for ~d and ~r modifiers (mutt grammar).

IMAP's ``SEARCH`` is day-granular: ``SINCE``/``BEFORE``/``ON`` only know
calendar dates, not timestamps. Whole-day offsets (``y``, ``m``, ``w``,
``d``) round cleanly. Sub-day offsets (``H``, ``M``, ``S``) are emitted as
the corresponding day boundary on the server *and* paired with a Python
predicate so the client can refine the result set after fetching the
candidate messages, recovering minute/second precision.
"""

from __future__ import annotations

import email.utils
import re
from datetime import date, datetime, timedelta, timezone
from typing import Callable

OFFSET_UNITS = {
    "y": lambda n: timedelta(days=n * 365),
    "m": lambda n: timedelta(days=n * 30),
    "w": lambda n: timedelta(weeks=n),
    "d": lambda n: timedelta(days=n),
    "H": lambda n: timedelta(hours=n),
    "M": lambda n: timedelta(minutes=n),
    "S": lambda n: timedelta(seconds=n),
}

_OFFSET_RE = re.compile(r"^(\d+)([ymwdHMS])$")
_ISO_RE = re.compile(r"(\d{4})-(\d{1,2})-(\d{1,2})")
_DMY_RE = re.compile(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?")

# Predicate signature: (rfc822_message, internaldate) -> bool.
# ``rfc822_message`` is the parsed email.message.Message.
# ``internaldate`` is the timezone-aware datetime from IMAP INTERNALDATE.
MessagePredicate = Callable[[object, datetime], bool]


def now() -> datetime:
    """Current time as a timezone-aware UTC datetime. Override-able for tests."""
    return datetime.now(timezone.utc)


def parse_offset(s: str) -> timedelta | None:
    """Parse N<unit> (e.g. '7d', '2w', '30M') into a timedelta."""
    m = _OFFSET_RE.match(s)
    if not m:
        return None
    return OFFSET_UNITS[m.group(2)](int(m.group(1)))


def offset_days(delta: timedelta) -> int:
    """Whole days, rounded toward zero. IMAP search is day-granular."""
    return delta.days


def _is_subday(s: str) -> bool:
    """Whether the offset string uses a sub-day unit (``H``/``M``/``S``)."""
    m = _OFFSET_RE.match(s)
    return bool(m) and m.group(2) in ("H", "M", "S")


def consume_date(s: str, i: int) -> tuple[date | None, int]:
    """Try to parse an absolute date at s[i:]. Returns (date, end_index) or (None, i).

    Accepts ISO ``YYYY-MM-DD`` and mutt ``D/M[/Y]``. Two-digit years <70 expand
    to 20xx, otherwise to 19xx (matches mutt's behavior).
    """
    m = _ISO_RE.match(s[i:])
    if m:
        try:
            return (date(int(m.group(1)), int(m.group(2)), int(m.group(3))), i + m.end())
        except ValueError:
            return (None, i)
    m = _DMY_RE.match(s[i:])
    if m:
        d, mo = int(m.group(1)), int(m.group(2))
        y_str = m.group(3)
        if y_str is None:
            y = date.today().year
        else:
            y = int(y_str)
            if y < 100:
                y += 2000 if y < 70 else 1900
        try:
            return (date(y, mo, d), i + m.end())
        except ValueError:
            return (None, i)
    return (None, i)


def fmt_imap_date(d: date) -> str:
    return d.strftime("%d-%b-%Y")


def _msg_date(msg: object, modifier: str, internaldate: datetime) -> datetime | None:
    """Return the timestamp the predicate should compare against, in UTC.

    For ``~d``, parse the ``Date:`` header. For ``~r``, use the IMAP
    INTERNALDATE we fetched alongside the message. Returns ``None`` if the
    Date header is missing or unparseable, in which case the caller should
    keep the message (server-side day filtering already approved it).
    """
    if modifier == "r":
        return internaldate.astimezone(timezone.utc) if internaldate else None
    raw = getattr(msg, "get", lambda _name, _default=None: None)("Date")
    if not raw:
        return None
    try:
        d = email.utils.parsedate_to_datetime(raw)
    except (TypeError, ValueError):
        return None
    if d is None:
        return None
    if d.tzinfo is None:
        d = d.replace(tzinfo=timezone.utc)
    return d.astimezone(timezone.utc)


def _make_predicate(op: str, threshold: datetime, modifier: str) -> MessagePredicate:
    """Build a predicate that compares a message's timestamp to ``threshold``."""
    if op == "<":
        # Newer than threshold (msg_date > threshold)
        def pred(msg: object, internaldate: datetime) -> bool:
            d = _msg_date(msg, modifier, internaldate)
            return d is None or d > threshold

        return pred
    if op == ">":

        def pred(msg: object, internaldate: datetime) -> bool:
            d = _msg_date(msg, modifier, internaldate)
            return d is None or d < threshold

        return pred

    # op == '='
    # No useful sub-day "exactly N units old" semantic; widen to 1-second.
    def pred(msg: object, internaldate: datetime) -> bool:
        d = _msg_date(msg, modifier, internaldate)
        if d is None:
            return True
        return abs((d - threshold).total_seconds()) < 1

    return pred


def parse_daterange(
    s: str,
    modifier: str,
    today: date | None = None,
) -> tuple[str, MessagePredicate | None]:
    """Parse a mutt DATERANGE for the given modifier ('d' or 'r').

    ``d`` maps to ``SENTSINCE/SENTBEFORE/SENTON`` (Date: header).
    ``r`` maps to ``SINCE/BEFORE/ON`` (INTERNALDATE).

    Returns ``(criteria, predicate)``. ``criteria`` is the IMAP search
    criteria string. ``predicate`` is non-None only for sub-day offsets
    (``H``/``M``/``S``); the caller should apply it to candidate messages
    after the server-side day-granular search to recover precision.
    Raises ``ValueError`` on bad input.
    """
    keys = ("SENTSINCE", "SENTBEFORE", "SENTON") if modifier == "d" else ("SINCE", "BEFORE", "ON")
    s = s.strip()
    if not s:
        raise ValueError(f"~{modifier} requires a date range argument")
    if today is None:
        today = date.today()

    if s[0] in "<>=":
        op = s[0]
        offset_s = s[1:]
        delta = parse_offset(offset_s)
        if delta is None:
            raise ValueError(f"invalid offset after {op!r}: {offset_s!r}")
        target = today - timedelta(days=offset_days(delta))
        if op == "<":
            criteria = f"{keys[0]} {fmt_imap_date(target)}"
        elif op == ">":
            criteria = f"{keys[1]} {fmt_imap_date(target)}"
        else:
            criteria = f"{keys[2]} {fmt_imap_date(target)}"
        predicate = None
        if _is_subday(offset_s):
            predicate = _make_predicate(op, now() - delta, modifier)
        return (criteria, predicate)

    # Legacy bare offset: 7d → <7d. Build the same predicate as <Nu so
    # legacy callers also benefit from sub-day precision.
    if _OFFSET_RE.match(s):
        delta = parse_offset(s)
        assert delta is not None
        target = today - timedelta(days=offset_days(delta))
        criteria = f"{keys[0]} {fmt_imap_date(target)}"
        predicate = None
        if _is_subday(s):
            predicate = _make_predicate("<", now() - delta, modifier)
        return (criteria, predicate)

    # Absolute-date forms are inherently day-granular; no predicate needed.
    if s.startswith("-"):
        d, end = consume_date(s, 1)
        if d is None or end != len(s):
            raise ValueError(f'expected date after "-": {s!r}')
        return (f"{keys[1]} {fmt_imap_date(d)}", None)

    d1, i = consume_date(s, 0)
    if d1 is None:
        raise ValueError(f"cannot parse date range: {s!r}")
    rest = s[i:]

    if not rest:
        return (f"{keys[2]} {fmt_imap_date(d1)}", None)

    if rest == "-":
        return (f"{keys[0]} {fmt_imap_date(d1)}", None)

    if rest.startswith("-"):
        d2, end = consume_date(rest, 1)
        if d2 is None or end != len(rest):
            raise ValueError(f'expected date after "-": {s!r}')
        criteria = (
            f"{keys[0]} {fmt_imap_date(d1)} {keys[1]} {fmt_imap_date(d2 + timedelta(days=1))}"
        )
        return (criteria, None)

    if rest[0] in ("*", "±"):
        delta = parse_offset(rest[1:])
        if delta is None:
            raise ValueError(f"invalid margin offset: {rest[1:]!r}")
        days = offset_days(delta)
        criteria = (
            f"{keys[0]} {fmt_imap_date(d1 - timedelta(days=days))} "
            f"{keys[1]} {fmt_imap_date(d1 + timedelta(days=days + 1))}"
        )
        return (criteria, None)

    raise ValueError(f"unrecognized date range tail: {s!r}")
