"""Date-range parsing tests. ``today`` is pinned for stability."""

from __future__ import annotations

import email
from datetime import date, datetime, timedelta, timezone

import pytest

from muttlike_imap.dates import (
    consume_date,
    fmt_imap_date,
    offset_days,
    parse_daterange,
    parse_offset,
)

TODAY = date(2026, 4, 30)
NOW_UTC = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)


class TestParseOffset:
    @pytest.mark.parametrize(
        ("s", "expected_days"),
        [
            ("7d", 7),
            ("2w", 14),
            ("3m", 90),
            ("1y", 365),
            ("0d", 0),
        ],
    )
    def test_day_units(self, s, expected_days):
        delta = parse_offset(s)
        assert delta is not None
        assert offset_days(delta) == expected_days

    @pytest.mark.parametrize("s", ["1H", "30M", "45S"])
    def test_subday_rounds_to_zero_days(self, s):
        delta = parse_offset(s)
        assert delta is not None
        assert offset_days(delta) == 0

    @pytest.mark.parametrize("s", ["", "7", "d", "7x", "abc", "-7d", "7days"])
    def test_invalid(self, s):
        assert parse_offset(s) is None


class TestConsumeDate:
    def test_iso(self):
        d, end = consume_date("2025-04-30 trailing", 0)
        assert d == date(2025, 4, 30)
        assert end == 10

    def test_dmy_full(self):
        d, end = consume_date("30/4/2025", 0)
        assert d == date(2025, 4, 30)
        assert end == 9

    def test_dmy_two_digit_year_2000s(self):
        d, _ = consume_date("30/4/25", 0)
        assert d == date(2025, 4, 30)

    def test_dmy_two_digit_year_1900s(self):
        d, _ = consume_date("30/4/85", 0)
        assert d == date(1985, 4, 30)

    def test_dmy_omitted_year_uses_current(self, monkeypatch):
        import muttlike_imap.dates as dates_mod

        monkeypatch.setattr(dates_mod, "date", _fixed_date_class(TODAY))
        d, _ = consume_date("30/4", 0)
        assert d == date(TODAY.year, 4, 30)

    def test_dmy_invalid_day(self):
        d, end = consume_date("32/4/2025", 0)
        assert d is None
        assert end == 0

    def test_no_match(self):
        d, end = consume_date("not a date", 0)
        assert d is None
        assert end == 0


def _fixed_date_class(today_value):
    class FixedDate(date):
        @classmethod
        def today(cls):
            return today_value

    return FixedDate


@pytest.fixture
def fixed_today(monkeypatch):
    import muttlike_imap.dates as dates_mod

    monkeypatch.setattr(dates_mod, "date", _fixed_date_class(TODAY))
    monkeypatch.setattr(dates_mod, "now", lambda: NOW_UTC)
    return TODAY


class TestParseDaterangeRelative:
    def test_less_than(self, fixed_today):
        criteria, predicate = parse_daterange("<7d", "d")
        assert criteria == "SENTSINCE 23-Apr-2026"
        # Whole-day offset → no post-filter predicate.
        assert predicate is None

    def test_greater_than(self, fixed_today):
        criteria, predicate = parse_daterange(">7d", "d")
        assert criteria == "SENTBEFORE 23-Apr-2026"
        assert predicate is None

    def test_equals(self, fixed_today):
        criteria, predicate = parse_daterange("=1y", "d")
        assert criteria == "SENTON 30-Apr-2025"
        assert predicate is None

    def test_legacy_bare_offset(self, fixed_today):
        criteria, predicate = parse_daterange("7d", "d")
        assert criteria == "SENTSINCE 23-Apr-2026"
        assert predicate is None

    def test_subday_emits_predicate(self, fixed_today):
        criteria, predicate = parse_daterange("<30M", "d")
        assert criteria == "SENTSINCE 30-Apr-2026"
        assert predicate is not None

    def test_received_modifier_uses_internaldate_keys(self, fixed_today):
        criteria, predicate = parse_daterange("<3d", "r")
        assert criteria == "SINCE 27-Apr-2026"
        assert predicate is None


class TestParseDaterangeAbsolute:
    def test_iso_single(self):
        criteria, predicate = parse_daterange("2025-04-30", "d")
        assert criteria == "SENTON 30-Apr-2025"
        assert predicate is None

    def test_dmy_single(self):
        criteria, _ = parse_daterange("30/4/2025", "d")
        assert criteria == "SENTON 30-Apr-2025"

    def test_iso_range(self):
        criteria, _ = parse_daterange("2025-01-01-2025-12-31", "d")
        assert criteria == "SENTSINCE 01-Jan-2025 SENTBEFORE 01-Jan-2026"

    def test_dmy_range(self):
        criteria, _ = parse_daterange("1/1/2025-31/12/2025", "d")
        assert criteria == "SENTSINCE 01-Jan-2025 SENTBEFORE 01-Jan-2026"

    def test_half_open_before(self):
        criteria, _ = parse_daterange("-2025-04-30", "d")
        assert criteria == "SENTBEFORE 30-Apr-2025"

    def test_half_open_since(self):
        criteria, _ = parse_daterange("2025-04-30-", "d")
        assert criteria == "SENTSINCE 30-Apr-2025"

    def test_error_margin(self):
        criteria, _ = parse_daterange("2025-04-30*2w", "d")
        assert criteria == "SENTSINCE 16-Apr-2025 SENTBEFORE 15-May-2025"


class TestParseDaterangeErrors:
    @pytest.mark.parametrize(
        "s",
        [
            "",
            "invalid",
            "<",
            "<7x",
            "<abc",
            "-",
            "-notadate",
            "2025-04-30-notadate",
            "2025-04-30*",
            "2025-04-30*7x",
        ],
    )
    def test_raises(self, s):
        with pytest.raises(ValueError):
            parse_daterange(s, "d")


def _msg_with_date(date_header: str) -> email.message.Message:
    """Build a minimal RFC822 message with the given Date: header."""
    msg = email.message.Message()
    msg["Date"] = date_header
    return msg


class TestSubdayPredicate:
    """The post-filter predicate for sub-day offsets."""

    def test_lt_keeps_recent(self, fixed_today):
        # <30M: messages newer than 11:30Z should pass; older should fail.
        _, predicate = parse_daterange("<30M", "d")
        assert predicate is not None
        recent = _msg_with_date("Thu, 30 Apr 2026 11:45:00 +0000")
        old = _msg_with_date("Thu, 30 Apr 2026 09:00:00 +0000")
        assert predicate(recent, NOW_UTC)
        assert not predicate(old, NOW_UTC)

    def test_gt_keeps_old(self, fixed_today):
        # >30M: messages older than 11:30Z should pass; newer should fail.
        _, predicate = parse_daterange(">30M", "d")
        assert predicate is not None
        recent = _msg_with_date("Thu, 30 Apr 2026 11:45:00 +0000")
        old = _msg_with_date("Thu, 30 Apr 2026 09:00:00 +0000")
        assert not predicate(recent, NOW_UTC)
        assert predicate(old, NOW_UTC)

    def test_unparseable_date_is_kept(self, fixed_today):
        # Server already approved the message via day-granular SEARCH;
        # keep it if we can't parse the Date header.
        _, predicate = parse_daterange("<30M", "d")
        assert predicate is not None
        bogus = _msg_with_date("not a date at all")
        assert predicate(bogus, NOW_UTC)

    def test_missing_date_is_kept(self, fixed_today):
        _, predicate = parse_daterange("<30M", "d")
        assert predicate is not None
        msg = email.message.Message()  # no Date header
        assert predicate(msg, NOW_UTC)

    def test_received_modifier_uses_internaldate(self, fixed_today):
        # ~r predicate compares against INTERNALDATE, ignoring Date: header.
        _, predicate = parse_daterange("<30M", "r")
        assert predicate is not None
        msg = _msg_with_date("Thu, 1 Jan 1990 00:00:00 +0000")  # ancient Date
        recent_id = NOW_UTC - timedelta(minutes=10)
        old_id = NOW_UTC - timedelta(hours=2)
        assert predicate(msg, recent_id)
        assert not predicate(msg, old_id)

    def test_legacy_bare_subday_offset_emits_predicate(self, fixed_today):
        _, predicate = parse_daterange("30M", "d")
        assert predicate is not None


def test_fmt_imap_date():
    assert fmt_imap_date(date(2025, 1, 5)) == "05-Jan-2025"


def test_offset_days_negative():
    assert offset_days(timedelta(days=-3)) == -3
