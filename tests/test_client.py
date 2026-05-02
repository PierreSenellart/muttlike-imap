"""Tests for client.py using a fake IMAP server."""

from __future__ import annotations

import email
import imaplib
from datetime import datetime, timedelta, timezone

import pytest

from muttlike_imap import client

# ---------- Pure helpers ----------


def test_decode_header_empty():
    assert client.decode_header("") == ""


def test_decode_header_plain():
    assert client.decode_header("Hello") == "Hello"


def test_decode_header_encoded_word():
    # MIME-encoded UTF-8 subject
    encoded = "=?UTF-8?B?SMOpbGxvIHfDtnJsZA==?="
    assert client.decode_header(encoded) == "Héllo wörld"


def test_decode_header_unknown_charset():
    # Some mailers emit unknown-8bit; must not raise, falls back to utf-8
    encoded = "=?unknown-8bit?Q?Hello?="
    result = client.decode_header(encoded)
    assert "Hello" in result


def test_get_preview_plain():
    msg = email.message_from_string("Subject: x\n\nHello world")
    assert client.get_preview(msg).startswith("Hello world")


def test_get_preview_truncates():
    body = "x" * 1000
    msg = email.message_from_string(f"Subject: x\n\n{body}")
    assert len(client.get_preview(msg, max_chars=50)) == 50


def test_get_preview_strips_html_when_no_text_part():
    # Multipart with only an HTML part: the html-stripping branch fires.
    raw = (
        "MIME-Version: 1.0\n"
        'Content-Type: multipart/alternative; boundary="b"\n\n'
        "--b\nContent-Type: text/html\n\n<p>Hello <b>world</b></p>\n"
        "--b--\n"
    )
    msg = email.message_from_string(raw)
    out = client.get_preview(msg)
    assert "Hello" in out
    assert "<" not in out


def test_get_preview_multipart_prefers_text():
    raw = (
        "MIME-Version: 1.0\n"
        'Content-Type: multipart/alternative; boundary="b"\n\n'
        "--b\nContent-Type: text/plain\n\nplain version\n"
        "--b\nContent-Type: text/html\n\n<p>html version</p>\n"
        "--b--\n"
    )
    msg = email.message_from_string(raw)
    assert client.get_preview(msg) == "plain version"


# ---------- Fake IMAP server ----------


class FakeIMAP:
    """In-memory IMAP4_SSL stand-in."""

    def __init__(self, host, port=993):
        self.host = host
        self.port = port
        self.logged_in = False
        self.selected = None
        self.search_calls = []
        # Behaviour knobs set by tests
        self.search_responses: list = []
        self.fetch_responses: dict = {}
        self.list_response = ("OK", [])
        self.select_responses: dict = {}

    def login(self, user, password):
        self.logged_in = True
        return ("OK", [b"Logged in"])

    def logout(self):
        return ("BYE", [b""])

    def select(self, mailbox, readonly=False):
        self.selected = mailbox
        if mailbox in self.select_responses:
            return self.select_responses[mailbox]
        return ("OK", [b"1"])

    def search(self, charset, criteria):
        self.search_calls.append((charset, criteria))
        if not self.search_responses:
            return ("OK", [b""])
        resp = self.search_responses.pop(0)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def fetch(self, uid, what):
        if uid in self.fetch_responses:
            return self.fetch_responses[uid]
        # Return a minimal RFC822 message with a default INTERNALDATE.
        raw = b"From: a@example.com\r\nSubject: hi\r\n\r\nbody"
        prefix = (
            b'1 (INTERNALDATE "01-Jan-2026 00:00:00 +0000" RFC822 {' + str(len(raw)).encode() + b"}"
        )
        return ("OK", [(prefix, raw)])

    def list(self, directory='""', pattern="*"):
        return self.list_response


@pytest.fixture
def fake_imap(monkeypatch):
    """Patch imaplib.IMAP4_SSL to return a controllable fake. Yields the instance."""
    instances: list[FakeIMAP] = []

    def factory(host, port=993):
        inst = FakeIMAP(host, port)
        instances.append(inst)
        return inst

    monkeypatch.setattr(imaplib, "IMAP4_SSL", factory)
    return instances


def _config():
    return {
        "HOST": "imap.example.com",
        "PORT": "993",
        "USER": "me@example.com",
        "PASS": "secret",
        "TLS": "true",
    }


# ---------- imap_connect ----------


class TestImapConnect:
    def test_missing_host(self):
        with pytest.raises(RuntimeError, match="host not configured"):
            client.imap_connect({"USER": "x", "PASS": "y"})

    def test_missing_user(self):
        with pytest.raises(RuntimeError, match="user not configured"):
            client.imap_connect({"HOST": "x", "PASS": "y"})

    def test_missing_password(self):
        with pytest.raises(RuntimeError, match="password not configured"):
            client.imap_connect({"HOST": "x", "USER": "y"})

    def test_logs_in(self, fake_imap):
        imap = client.imap_connect(_config())
        assert imap.logged_in
        assert imap.host == "imap.example.com"


# ---------- list_mailboxes ----------


def test_list_mailboxes(fake_imap):
    instances = fake_imap

    def factory(host, port=993):
        inst = FakeIMAP(host, port)
        inst.list_response = (
            "OK",
            [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Sent"'],
        )
        instances.append(inst)
        return inst

    imaplib.IMAP4_SSL = factory  # already patched, just reset to the customized one
    assert client.list_mailboxes(_config()) == ["INBOX", "Sent"]


# ---------- search ----------


class TestSearch:
    def _patch_with_search_results(self, monkeypatch, search_responses, fetch_message=None):
        def factory(host, port=993):
            inst = FakeIMAP(host, port)
            inst.search_responses = list(search_responses)
            if fetch_message is not None:
                inst.fetch_responses = {b"1": ("OK", [(b"1 (RFC822 {0})", fetch_message)])}
            self.inst = inst
            return inst

        monkeypatch.setattr(imaplib, "IMAP4_SSL", factory)

    def test_no_matches(self, monkeypatch):
        self._patch_with_search_results(monkeypatch, [("OK", [b""])])
        assert client.search(_config(), "~U", limit=10, mailbox="INBOX") == []

    def test_returns_results(self, monkeypatch):
        msg = (
            b"From: alice@example.com\r\n"
            b"To: me@example.com\r\n"
            b"Subject: hello\r\n"
            b"Date: Wed, 1 Jan 2025 12:00:00 +0000\r\n"
            b"\r\nbody"
        )
        self._patch_with_search_results(monkeypatch, [("OK", [b"1"])], fetch_message=msg)
        results = client.search(_config(), "~U", limit=10, mailbox="INBOX")
        assert len(results) == 1
        assert results[0]["from"] == "alice@example.com"
        assert results[0]["subject"] == "hello"
        assert results[0]["preview"] == "body"
        assert "body" not in results[0]

    def test_include_body_adds_body_key(self, monkeypatch):
        long_text = b"x" * 1000
        msg = b"From: alice@example.com\r\nSubject: s\r\n\r\n" + long_text
        self._patch_with_search_results(monkeypatch, [("OK", [b"1"])], fetch_message=msg)
        results = client.search(_config(), "~U", limit=10, mailbox="INBOX", include_body=True)
        assert len(results) == 1
        assert results[0]["body"] == "x" * 1000
        assert len(results[0]["preview"]) == 300

    def test_unicode_mailbox_encoded_to_utf7(self, monkeypatch):
        self._patch_with_search_results(monkeypatch, [("OK", [b""])])
        client.search(_config(), "~U", limit=10, mailbox="Éléments envoyés")
        # The fake records the SELECT mailbox name; non-ASCII gets mUTF-7'd.
        assert self.inst.selected == "&AMk-l&AOk-ments envoy&AOk-s"

    def test_ascii_mailbox_passed_through(self, monkeypatch):
        self._patch_with_search_results(monkeypatch, [("OK", [b""])])
        client.search(_config(), "~U", limit=10, mailbox="Sent")
        assert self.inst.selected == "Sent"

    def test_select_failure_raises(self, monkeypatch):
        def factory(host, port=993):
            inst = FakeIMAP(host, port)
            inst.select_responses = {"NoSuch": ("NO", [b"missing"])}
            return inst

        monkeypatch.setattr(imaplib, "IMAP4_SSL", factory)
        with pytest.raises(RuntimeError, match="cannot select"):
            client.search(_config(), "~U", limit=10, mailbox="NoSuch")

    def test_fold_only_retry_on_imap_error(self, monkeypatch):
        # First search call raises, second succeeds. Pattern has accented text
        # so the retry actually changes the criteria string.
        self._patch_with_search_results(
            monkeypatch,
            [imaplib.IMAP4.error("8-bit"), ("OK", [b""])],
        )
        client.search(_config(), "~f Müller", limit=10, mailbox="INBOX")
        # Two search calls landed: original then ascii-folded.
        assert len(self.inst.search_calls) == 2
        assert b"Muller" in self.inst.search_calls[1][1]

    def test_limit_truncates_oldest(self, monkeypatch):
        msg = b"From: a@x\r\nSubject: s\r\n\r\nbody"

        def factory(host, port=993):
            inst = FakeIMAP(host, port)
            inst.search_responses = [("OK", [b"1 2 3 4 5"])]
            for uid in [b"3", b"4", b"5"]:
                prefix = b'x (INTERNALDATE "01-Jan-2026 00:00:00 +0000" RFC822 {N}'
                inst.fetch_responses[uid] = ("OK", [(prefix, msg)])
            self.inst = inst
            return inst

        monkeypatch.setattr(imaplib, "IMAP4_SSL", factory)
        results = client.search(_config(), "~U", limit=3, mailbox="INBOX")
        # The script keeps the LAST `limit` UIDs, then reverses (most-recent first)
        assert [r["uid"] for r in results] == ["5", "4", "3"]


class TestFetchByUids:
    def _patch(self, monkeypatch, fetch_responses=None):
        def factory(host, port=993):
            inst = FakeIMAP(host, port)
            if fetch_responses:
                inst.fetch_responses = fetch_responses
            self.inst = inst
            return inst

        monkeypatch.setattr(imaplib, "IMAP4_SSL", factory)

    def test_returns_records_for_given_uids(self, monkeypatch):
        raw = b"From: bob@example.com\r\nSubject: hi\r\n\r\nhello world"
        prefix = b'1 (INTERNALDATE "01-Jan-2026 00:00:00 +0000" RFC822 {N}'
        self._patch(monkeypatch, {b"42": ("OK", [(prefix, raw)])})
        results = client.fetch_by_uids(_config(), ["42"], mailbox="INBOX")
        assert len(results) == 1
        assert results[0]["uid"] == "42"
        assert results[0]["from"] == "bob@example.com"
        assert results[0]["preview"] == "hello world"
        assert "body" not in results[0]

    def test_include_body(self, monkeypatch):
        long_text = b"y" * 500
        raw = b"From: bob@example.com\r\nSubject: s\r\n\r\n" + long_text
        prefix = b'1 (INTERNALDATE "01-Jan-2026 00:00:00 +0000" RFC822 {N}'
        self._patch(monkeypatch, {b"7": ("OK", [(prefix, raw)])})
        results = client.fetch_by_uids(_config(), ["7"], include_body=True)
        assert results[0]["body"] == "y" * 500
        assert len(results[0]["preview"]) == 300

    def test_select_failure_raises(self, monkeypatch):
        def factory(host, port=993):
            inst = FakeIMAP(host, port)
            inst.select_responses = {"NoSuch": ("NO", [b"missing"])}
            return inst

        monkeypatch.setattr(imaplib, "IMAP4_SSL", factory)
        with pytest.raises(RuntimeError, match="cannot select"):
            client.fetch_by_uids(_config(), ["1"], mailbox="NoSuch")

    def test_skips_failed_fetch(self, monkeypatch):
        self._patch(monkeypatch, {b"99": ("NO", [None])})
        results = client.fetch_by_uids(_config(), ["99"])
        assert results == []

    def test_multiple_uids_ordered(self, monkeypatch):
        def make_raw(sender: str) -> bytes:
            return f"From: {sender}\r\nSubject: s\r\n\r\nbody".encode()

        prefix = b'1 (INTERNALDATE "01-Jan-2026 00:00:00 +0000" RFC822 {N}'
        self._patch(
            monkeypatch,
            {
                b"10": ("OK", [(prefix, make_raw("a@x"))]),
                b"20": ("OK", [(prefix, make_raw("b@x"))]),
            },
        )
        results = client.fetch_by_uids(_config(), ["10", "20"])
        assert [r["uid"] for r in results] == ["10", "20"]
        assert results[0]["from"] == "a@x"
        assert results[1]["from"] == "b@x"


def _msg_with_date(date_header: str) -> bytes:
    return f"From: a@x\r\nSubject: s\r\nDate: {date_header}\r\n\r\nbody".encode()


def _fetch_response(internaldate: str, raw: bytes) -> tuple[str, list]:
    prefix = b'x (INTERNALDATE "' + internaldate.encode() + b'" RFC822 {N}'
    return ("OK", [(prefix, raw)])


class TestSearchPostFilter:
    """When the pattern has a sub-day predicate, the client walks all UIDs
    newest-first, applies the predicate, and stops at ``limit``."""

    def test_subday_filter_drops_old_messages(self, monkeypatch):
        # Three messages from today; only one is within the last 30 minutes.
        # The post-filter should keep only that one.
        recent = _msg_with_date("Thu, 30 Apr 2026 11:50:00 +0000")  # 10 min ago
        old = _msg_with_date("Thu, 30 Apr 2026 09:00:00 +0000")  # 3h ago

        def factory(host, port=993):
            inst = FakeIMAP(host, port)
            inst.search_responses = [("OK", [b"1 2 3"])]
            inst.fetch_responses[b"1"] = _fetch_response("30-Apr-2026 09:00:00 +0000", old)
            inst.fetch_responses[b"2"] = _fetch_response("30-Apr-2026 09:00:00 +0000", old)
            inst.fetch_responses[b"3"] = _fetch_response("30-Apr-2026 11:50:00 +0000", recent)
            self.inst = inst
            return inst

        monkeypatch.setattr(imaplib, "IMAP4_SSL", factory)
        # Pin "now" so the predicate threshold is stable.
        import muttlike_imap.dates as dates_mod

        monkeypatch.setattr(
            dates_mod, "now", lambda: datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)
        )

        results = client.search(_config(), "~d <30M", limit=10, mailbox="INBOX")
        assert [r["uid"] for r in results] == ["3"]

    def test_subday_walks_until_limit_reached(self, monkeypatch):
        # 5 candidates, all match the predicate. Should stop after ``limit``.
        recent = _msg_with_date("Thu, 30 Apr 2026 11:50:00 +0000")

        def factory(host, port=993):
            inst = FakeIMAP(host, port)
            inst.search_responses = [("OK", [b"1 2 3 4 5"])]
            for uid in [b"1", b"2", b"3", b"4", b"5"]:
                inst.fetch_responses[uid] = _fetch_response("30-Apr-2026 11:50:00 +0000", recent)
            self.inst = inst
            return inst

        monkeypatch.setattr(imaplib, "IMAP4_SSL", factory)
        import muttlike_imap.dates as dates_mod

        monkeypatch.setattr(
            dates_mod, "now", lambda: datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)
        )

        results = client.search(_config(), "~d <30M", limit=2, mailbox="INBOX")
        # Newest-first walk stops after 2 matches.
        assert [r["uid"] for r in results] == ["5", "4"]

    def test_subday_returns_fewer_than_limit_when_few_match(self, monkeypatch):
        recent = _msg_with_date("Thu, 30 Apr 2026 11:50:00 +0000")
        old = _msg_with_date("Thu, 30 Apr 2026 09:00:00 +0000")

        def factory(host, port=993):
            inst = FakeIMAP(host, port)
            inst.search_responses = [("OK", [b"1 2 3"])]
            inst.fetch_responses[b"1"] = _fetch_response("30-Apr-2026 11:50:00 +0000", recent)
            inst.fetch_responses[b"2"] = _fetch_response("30-Apr-2026 09:00:00 +0000", old)
            inst.fetch_responses[b"3"] = _fetch_response("30-Apr-2026 09:00:00 +0000", old)
            self.inst = inst
            return inst

        monkeypatch.setattr(imaplib, "IMAP4_SSL", factory)
        import muttlike_imap.dates as dates_mod

        monkeypatch.setattr(
            dates_mod, "now", lambda: datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)
        )
        results = client.search(_config(), "~d <30M", limit=10, mailbox="INBOX")
        # Only one matches; we get one back even though limit is 10.
        assert len(results) == 1
        assert results[0]["uid"] == "1"

    def test_or_with_subday_does_not_post_filter(self, monkeypatch):
        # An OR'd pattern shouldn't apply the predicate as a top-level filter
        # (would change the semantics). Both messages should come back even
        # though only one matches the date arm.
        recent = _msg_with_date("Thu, 30 Apr 2026 11:50:00 +0000")
        old = _msg_with_date("Thu, 30 Apr 2026 09:00:00 +0000")

        def factory(host, port=993):
            inst = FakeIMAP(host, port)
            inst.search_responses = [("OK", [b"1 2"])]
            inst.fetch_responses[b"1"] = _fetch_response("30-Apr-2026 09:00:00 +0000", old)
            inst.fetch_responses[b"2"] = _fetch_response("30-Apr-2026 11:50:00 +0000", recent)
            self.inst = inst
            return inst

        monkeypatch.setattr(imaplib, "IMAP4_SSL", factory)
        import muttlike_imap.dates as dates_mod

        monkeypatch.setattr(
            dates_mod, "now", lambda: datetime(2026, 4, 30, 12, 0, 0, tzinfo=timezone.utc)
        )
        results = client.search(_config(), "~d <30M | ~U", limit=10, mailbox="INBOX")
        # Both UIDs returned (server-side OR is the only filter; predicate dropped).
        assert sorted(r["uid"] for r in results) == ["1", "2"]


class TestInternaldateParsing:
    def test_parse_internaldate(self):
        blob = b'1 (INTERNALDATE "30-Apr-2026 11:45:00 +0200" RFC822 {123}'
        d = client._parse_internaldate(blob)
        assert d is not None
        assert d.year == 2026
        assert d.hour == 11
        assert d.minute == 45
        assert d.utcoffset() == timedelta(hours=2)

    def test_parse_internaldate_missing(self):
        assert client._parse_internaldate(b"1 (RFC822 {123}") is None

    def test_parse_internaldate_malformed(self):
        assert client._parse_internaldate(b'1 (INTERNALDATE "garbage" RFC822 {123}') is None
