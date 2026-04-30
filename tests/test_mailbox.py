"""Mailbox-name handling: UTF-7 round-trip + LIST parsing."""

from __future__ import annotations

import pytest

from muttlike_imap.mailbox import imap_utf7_decode, imap_utf7_encode, parse_list_response


@pytest.mark.parametrize(
    "name",
    [
        "INBOX",
        "Sent",
        "All Mail",
        "Brouillons",
        "Éléments envoyés",
        "A & B",
        "日本語",
        "café",
        "papers/wdm",
    ],
)
def test_utf7_round_trip(name):
    assert imap_utf7_decode(imap_utf7_encode(name)) == name


def test_utf7_known_encoding():
    # Spec example: "& B" → "&-B" via the literal-amp escape "&-".
    assert imap_utf7_encode("&") == "&-"
    assert imap_utf7_decode("&-") == "&"


def test_utf7_decode_known_value():
    # 'Éléments envoyés' is the canonical example from RFC 3501 §5.1.3 territory.
    assert imap_utf7_decode("&AMk-l&AOk-ments envoy&AOk-s") == "Éléments envoyés"


class TestParseListResponse:
    def test_basic(self):
        items = [
            b'(\\HasNoChildren) "/" "INBOX"',
            b'(\\HasChildren) "/" "Folders"',
            b'(\\HasNoChildren \\Sent) "/" "Sent"',
        ]
        assert parse_list_response(items) == ["INBOX", "Folders", "Sent"]

    def test_skips_noselect(self):
        items = [
            b'(\\HasChildren \\Noselect) "/" "[Gmail]"',
            b'(\\HasNoChildren) "/" "Inbox"',
        ]
        assert parse_list_response(items) == ["Inbox"]

    def test_decodes_utf7_names(self):
        items = [b'(\\HasNoChildren) "/" "&AMk-l&AOk-ments envoy&AOk-s"']
        assert parse_list_response(items) == ["Éléments envoyés"]

    def test_handles_none_in_response(self):
        items = [b'(\\HasNoChildren) "/" "INBOX"', None]
        assert parse_list_response(items) == ["INBOX"]

    def test_handles_unparseable_lines(self):
        items = [b"garbage line"]
        assert parse_list_response(items) == []

    def test_handles_unquoted_name(self):
        # Some servers omit quotes around ASCII names.
        items = [b'(\\HasNoChildren) "/" INBOX']
        assert parse_list_response(items) == ["INBOX"]
