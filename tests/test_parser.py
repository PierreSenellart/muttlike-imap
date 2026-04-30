"""Parser tests for the mutt-style pattern → IMAP criteria conversion."""

from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

from muttlike_imap.parser import (
    CompiledPattern,
    Parser,
    ascii_fold,
    build_or,
    compile_pattern,
    field_match,
    parse_pattern,
    quote,
)

TODAY = date(2026, 4, 30)
NOW_UTC = datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def fixed_today(monkeypatch):
    import muttlike_imap.dates as dates_mod

    class FixedDate(date):
        @classmethod
        def today(cls):
            return TODAY

    monkeypatch.setattr(dates_mod, "date", FixedDate)
    monkeypatch.setattr(dates_mod, "now", lambda: NOW_UTC)


# ---------- Helpers ----------


def test_quote_escapes():
    assert quote('a "b" \\c') == '"a \\"b\\" \\\\c"'


def test_ascii_fold_strips_diacritics():
    assert ascii_fold("Müller") == "Muller"
    assert ascii_fold("café") == "cafe"


def test_ascii_fold_leaves_cjk_empty():
    assert ascii_fold("日本語") == ""


def test_field_match_ascii():
    assert field_match("FROM", "alice") == 'FROM "alice"'


def test_field_match_unicode_or_fan_out():
    out = field_match("FROM", "Müller")
    assert out == 'OR FROM "Müller" FROM "Muller"'


def test_field_match_fold_only():
    assert field_match("FROM", "Müller", fold_only=True) == 'FROM "Muller"'


def test_field_match_cjk_no_fold():
    # Folded form is empty → original only, no OR fan-out.
    assert field_match("FROM", "日本語") == 'FROM "日本語"'


def test_build_or_single_returns_input():
    assert build_or(['SUBJECT "x"']) == 'SUBJECT "x"'


def test_build_or_multiple_nests():
    assert build_or(["A", "B", "C"]) == "OR (A) (OR (B) (C))"


# ---------- Single-modifier dispatch ----------


class TestNoArg:
    @pytest.mark.parametrize(
        ("pat", "expected"),
        [
            ("~A", "ALL"),
            ("~U", "UNSEEN"),
            ("~N", "UNSEEN"),
            ("~R", "SEEN"),
            ("~O", "SEEN"),
            ("~F", "FLAGGED"),
            ("~D", "DELETED"),
            ("~Q", "ANSWERED"),
        ],
    )
    def test_flag(self, pat, expected):
        assert parse_pattern(pat) == expected


class TestText:
    @pytest.mark.parametrize(
        ("pat", "expected"),
        [
            ("~f alice", 'FROM "alice"'),
            ("~t alice", 'TO "alice"'),
            ("~s hello", 'SUBJECT "hello"'),
            ("~b body", 'BODY "body"'),
            ("~B everywhere", 'TEXT "everywhere"'),
            ("~c bob", 'CC "bob"'),
            ("~e sender@x", 'HEADER Sender "sender@x"'),
            ("~i abc123", 'HEADER Message-ID "abc123"'),
            ("~y label", 'HEADER X-Label "label"'),
        ],
    )
    def test_simple(self, pat, expected):
        assert parse_pattern(pat) == expected

    def test_quoted_value_with_spaces(self):
        assert parse_pattern('~s "hello world"') == 'SUBJECT "hello world"'

    def test_C_match(self):
        assert parse_pattern("~C alice") == 'OR (OR TO "alice" CC "alice") BCC "alice"'

    def test_L_match(self):
        assert parse_pattern("~L alice") == 'OR (OR FROM "alice" TO "alice") CC "alice"'

    def test_x_match(self):
        assert parse_pattern("~x abc") == 'OR HEADER References "abc" HEADER In-Reply-To "abc"'

    def test_h_with_colon(self):
        assert parse_pattern('~h "X-Mailer: thunderbird"') == 'HEADER X-Mailer "thunderbird"'

    def test_h_without_colon_falls_back_to_TEXT(self):
        assert parse_pattern("~h plain") == 'TEXT "plain"'


class TestMe:
    def test_p_requires_me(self):
        with pytest.raises(ValueError, match="~p requires"):
            parse_pattern("~p")

    def test_P_requires_me(self):
        with pytest.raises(ValueError, match="~P requires"):
            parse_pattern("~P")

    def test_p_uses_me(self):
        assert parse_pattern("~p", me="me@x") == 'OR (OR TO "me@x" CC "me@x") BCC "me@x"'

    def test_P_uses_me(self):
        assert parse_pattern("~P", me="me@x") == 'FROM "me@x"'


class TestSize:
    def test_smaller(self):
        assert parse_pattern("~z <1M") == "SMALLER 1048576"

    def test_range(self):
        assert parse_pattern("~z 1K-10K") == "LARGER 1023 SMALLER 10241"


class TestDates:
    def test_relative(self, fixed_today):
        assert parse_pattern("~d <7d") == "SENTSINCE 23-Apr-2026"

    def test_legacy_bare_offset(self, fixed_today):
        assert parse_pattern("~d 7d") == "SENTSINCE 23-Apr-2026"

    def test_received(self, fixed_today):
        assert parse_pattern("~r <3d") == "SINCE 27-Apr-2026"

    def test_iso_range(self, fixed_today):
        assert (
            parse_pattern("~d 2025-01-01-2025-12-31")
            == "SENTSINCE 01-Jan-2025 SENTBEFORE 01-Jan-2026"
        )


# ---------- Boolean composition ----------


class TestBoolean:
    def test_and(self):
        assert parse_pattern("~f alice ~U") == 'FROM "alice" UNSEEN'

    def test_or_top_level(self):
        assert parse_pattern("~f alice | ~f bob") == 'OR (FROM "alice") (FROM "bob")'

    def test_negation(self):
        assert parse_pattern("!~f spam") == 'NOT (FROM "spam")'

    def test_parens_grouping(self):
        out = parse_pattern("(~f a | ~f b) ~U")
        assert out == '(OR (FROM "a") (FROM "b")) UNSEEN'

    def test_negation_of_disjunction(self):
        out = parse_pattern("!(~f a | ~f b)")
        assert out == 'NOT ((OR (FROM "a") (FROM "b")))'

    def test_three_way_or_nests(self):
        out = parse_pattern("~f a | ~f b | ~f c")
        # left-fold: (a | b) | c → OR ((OR a b)) c
        assert out == 'OR (OR (FROM "a") (FROM "b")) (FROM "c")'

    def test_bare_word_treated_as_TEXT(self):
        assert parse_pattern("hello") == 'TEXT "hello"'


# ---------- Errors ----------


class TestErrors:
    @pytest.mark.parametrize(
        "pat",
        [
            "~T",  # mutt-runtime modifier
            "~m 1-10",  # message-number range
            "~v",  # collapsed thread
        ],
    )
    def test_unsupported_modifier(self, pat):
        with pytest.raises(ValueError, match="mutt-runtime"):
            parse_pattern(pat)

    def test_unknown_modifier(self):
        with pytest.raises(ValueError, match="unknown modifier"):
            parse_pattern("~Z")

    def test_unmatched_paren(self):
        with pytest.raises(ValueError, match="missing closing paren"):
            parse_pattern("(~f a")

    def test_missing_modifier_letter(self):
        with pytest.raises(ValueError, match="expected modifier letter"):
            parse_pattern("~")

    def test_bad_date(self):
        with pytest.raises(ValueError):
            parse_pattern("~d invalid")

    def test_text_modifier_requires_arg(self):
        # ~f with nothing after it
        with pytest.raises(ValueError):
            parse_pattern("~f")


# ---------- Empty / whitespace ----------


class TestEmpty:
    def test_empty_string_is_ALL(self):
        assert parse_pattern("") == "ALL"

    def test_whitespace_only_is_ALL(self):
        assert parse_pattern("   ") == "ALL"


# ---------- Parser internals (lightweight) ----------


def test_parser_consumes_entire_input():
    p = Parser("~f a", fold_only=False, me="")
    p.parse()
    assert p.i == len(p.s)


# ---------- compile_pattern: predicate lifting rules ----------


class TestCompiledPattern:
    def test_no_subday_no_predicates(self):
        cp = compile_pattern("~f alice ~U")
        assert isinstance(cp, CompiledPattern)
        assert cp.predicates == []

    def test_top_level_subday_lifts(self, fixed_today):
        cp = compile_pattern("~d <30M")
        assert len(cp.predicates) == 1

    def test_top_level_conjunction_with_subday_lifts(self, fixed_today):
        cp = compile_pattern("~U ~d <30M ~f alice")
        assert len(cp.predicates) == 1

    def test_subday_inside_disjunction_does_not_lift(self, fixed_today):
        # `~d <30M | ~f alice` would mismatch if we filtered top-level by
        # the date predicate, since the OR allows messages that don't
        # match the date arm. The parser must drop the predicate here.
        cp = compile_pattern("~d <30M | ~f alice")
        assert cp.predicates == []

    def test_subday_inside_negation_does_not_lift(self, fixed_today):
        cp = compile_pattern("!~d <30M")
        assert cp.predicates == []

    def test_subday_inside_paren_disjunction_does_not_lift(self, fixed_today):
        cp = compile_pattern("(~d <30M | ~f alice) ~U")
        assert cp.predicates == []

    def test_subday_inside_paren_conjunction_lifts(self, fixed_today):
        # Parens around a pure conjunction don't disturb the top-level
        # context.
        cp = compile_pattern("(~U ~d <30M)")
        assert len(cp.predicates) == 1

    def test_two_subday_clauses_lift_both(self, fixed_today):
        cp = compile_pattern("~d <1H ~d >5M")
        assert len(cp.predicates) == 2

    def test_first_arm_predicate_dropped_when_or_seen(self, fixed_today):
        # The parser optimistically collects the left arm at top level,
        # then must roll it back once it finds the `|`.
        cp = compile_pattern("~d <30M | ~U")
        assert cp.predicates == []

    def test_parse_pattern_is_criteria_only(self, fixed_today):
        # Backwards-compat shim: parse_pattern still returns just a string.
        out = parse_pattern("~d <30M")
        assert isinstance(out, str)
        assert out.startswith("SENTSINCE ")
