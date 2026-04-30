"""Size-range parsing tests."""

from __future__ import annotations

import pytest

from muttlike_imap.sizes import parse_size, parse_size_range


class TestParseSize:
    @pytest.mark.parametrize(
        ("s", "expected"),
        [
            ("0", 0),
            ("100", 100),
            ("1K", 1024),
            ("1k", 1024),
            ("2M", 2 * 1024 * 1024),
            ("2m", 2 * 1024 * 1024),
        ],
    )
    def test_valid(self, s, expected):
        assert parse_size(s) == expected

    @pytest.mark.parametrize("s", ["", "abc", "1G", "-1", "1.5K"])
    def test_invalid(self, s):
        assert parse_size(s) is None


class TestParseSizeRange:
    def test_smaller(self):
        assert parse_size_range("<1M") == "SMALLER 1048576"

    def test_larger(self):
        assert parse_size_range(">100K") == "LARGER 102400"

    def test_inclusive_upper_via_dash_prefix(self):
        # -100 means ≤ 100 → SMALLER 101
        assert parse_size_range("-100") == "SMALLER 101"

    def test_inclusive_lower_via_dash_suffix(self):
        # 100- means ≥ 100 → LARGER 99
        assert parse_size_range("100-") == "LARGER 99"

    def test_range(self):
        # 1K-10K inclusive: LARGER 1023 SMALLER 10241
        assert parse_size_range("1K-10K") == "LARGER 1023 SMALLER 10241"

    def test_plain_n_widens_to_one_byte_slot(self):
        # IMAP can't express equality; widen.
        assert parse_size_range("100") == "LARGER 99 SMALLER 101"

    def test_zero_lower_bound_does_not_underflow(self):
        # Plain 0 → LARGER max(0-1, 0)=0 SMALLER 1
        assert parse_size_range("0") == "LARGER 0 SMALLER 1"

    @pytest.mark.parametrize("s", ["", "<", "<abc", "-", "-abc", "1K-abc", "abc"])
    def test_invalid(self, s):
        with pytest.raises(ValueError):
            parse_size_range(s)
