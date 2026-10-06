"""Tests for attachment listing and saving."""

from __future__ import annotations

import base64
import email
import imaplib
import io
import json
import os

import pytest

from muttlike_imap import cli, client
from muttlike_imap.output import format_saves, format_summary

PDF = b"%PDF-1.4 fake"


def _mime(*parts: str) -> bytes:
    """A multipart/mixed message with a text body followed by ``parts``."""
    head = (
        "From: alice@example.com\r\nSubject: files\r\nMessage-ID: <m@x>\r\n"
        'MIME-Version: 1.0\r\nContent-Type: multipart/mixed; boundary="b"\r\n\r\n'
        "--b\r\nContent-Type: text/plain\r\n\r\nsee attached\r\n"
    )
    return (head + "".join(f"--b\r\n{p}\r\n" for p in parts) + "--b--\r\n").encode()


def _b64_part(ctype: str, data: bytes, filename: str | None = None, disp="attachment") -> str:
    lines = [f"Content-Type: {ctype}", "Content-Transfer-Encoding: base64"]
    if filename is not None:
        lines.append(f'Content-Disposition: {disp}; filename="{filename}"')
    return "\r\n".join(lines) + "\r\n\r\n" + base64.b64encode(data).decode()


TWO = _mime(
    _b64_part("application/pdf", PDF, "report.pdf"),
    _b64_part("image/png", b"\x89PNG", "photo.png", disp="inline"),
)


# ---------- attachments() ----------


def test_no_attachments_in_plain_message():
    msg = email.message_from_bytes(b"Subject: x\r\n\r\nhello")
    assert client.attachments(msg) == []


def test_text_body_is_not_an_attachment():
    msg = email.message_from_bytes(TWO)
    found = client.attachments(msg)
    assert [(a["index"], a["filename"], a["content_type"]) for a in found] == [
        (1, "report.pdf", "application/pdf"),
        (2, "photo.png", "image/png"),
    ]
    assert found[0]["data"] == PDF
    assert found[0]["size"] == len(PDF)


def test_alternative_html_is_not_an_attachment():
    raw = (
        b'MIME-Version: 1.0\r\nContent-Type: multipart/alternative; boundary="a"\r\n\r\n'
        b"--a\r\nContent-Type: text/plain\r\n\r\nplain\r\n"
        b"--a\r\nContent-Type: text/html\r\n\r\n<p>html</p>\r\n--a--\r\n"
    )
    assert client.attachments(email.message_from_bytes(raw)) == []


def test_text_part_with_filename_is_an_attachment():
    raw = _mime("Content-Type: text/plain\r\nContent-Disposition: attachment\r\n\r\nnotes")
    found = client.attachments(email.message_from_bytes(raw))
    assert [(a["filename"], a["content_type"]) for a in found] == [("", "text/plain")]


def test_encoded_word_filename_is_decoded():
    raw = _mime(_b64_part("application/pdf", PDF, "=?UTF-8?B?csOpc3Vtw6kucGRm?="))
    found = client.attachments(email.message_from_bytes(raw))
    assert found[0]["filename"] == "résumé.pdf"


def test_rfc2231_filename_is_decoded():
    raw = _mime(
        "Content-Type: application/pdf\r\n"
        "Content-Disposition: attachment; filename*=UTF-8''r%C3%A9sum%C3%A9.pdf\r\n\r\nx"
    )
    found = client.attachments(email.message_from_bytes(raw))
    assert found[0]["filename"] == "résumé.pdf"


def test_attached_message_is_one_attachment():
    inner = "From: bob@example.com\r\nSubject: inner\r\n\r\ninner body"
    raw = _mime(
        "Content-Type: message/rfc822\r\nContent-Disposition: attachment\r\n\r\n" + inner,
    )
    found = client.attachments(email.message_from_bytes(raw))
    assert len(found) == 1
    assert found[0]["content_type"] == "message/rfc822"
    assert b"Subject: inner" in found[0]["data"]


def test_records_list_attachments_without_data():
    rec = client._record_for(b"1", email.message_from_bytes(TWO))
    assert rec["attachments"] == [
        {"index": 1, "filename": "report.pdf", "content_type": "application/pdf", "size": 13},
        {"index": 2, "filename": "photo.png", "content_type": "image/png", "size": 4},
    ]
    json.dumps(rec)  # serializable: no bytes left in it


def test_summary_shows_attachments():
    rec = client._record_for(b"1", email.message_from_bytes(TWO))
    out = format_summary([rec])
    assert "Attachment 1:report.pdf (application/pdf, 13B)" in out
    assert "Attachment 2:photo.png (image/png, 4B)" in out


# ---------- select_attachments() ----------


class TestSelect:
    found = client.attachments(email.message_from_bytes(TWO))

    def test_default_is_all(self):
        assert client.select_attachments(self.found, None) == (self.found, [])

    def test_by_index(self):
        chosen, unmatched = client.select_attachments(self.found, ["2"])
        assert [a["filename"] for a in chosen] == ["photo.png"]
        assert unmatched == []

    def test_by_name(self):
        chosen, _ = client.select_attachments(self.found, ["report.pdf"])
        assert [a["index"] for a in chosen] == [1]

    def test_keeps_message_order_and_dedups(self):
        chosen, _ = client.select_attachments(self.found, ["photo.png", "1", "2"])
        assert [a["index"] for a in chosen] == [1, 2]

    def test_unmatched(self):
        chosen, unmatched = client.select_attachments(self.found, ["7", "nope.doc"])
        assert chosen == []
        assert unmatched == ["7", "nope.doc"]


# ---------- file names ----------


@pytest.mark.parametrize(
    "name, expected",
    [
        ("report.pdf", "report.pdf"),
        ("../../.bashrc", "bashrc"),
        ("/etc/passwd", "passwd"),
        ("C:\\Users\\x\\evil.exe", "evil.exe"),
        ("a\x00b\nc.txt", "abc.txt"),
        ("..", "attachment-3.pdf"),
        ("", "attachment-3.pdf"),
    ],
)
def test_safe_filename(name, expected):
    a = {"index": 3, "filename": name, "content_type": "application/pdf"}
    assert client._safe_filename(a) == expected


def test_write_new_never_overwrites(tmp_path):
    (tmp_path / "r.pdf").write_bytes(b"old")
    (tmp_path / "r-1.pdf").write_bytes(b"older")
    path = client._write_new(str(tmp_path), "r.pdf", b"new")
    assert path == str(tmp_path / "r-2.pdf")
    assert (tmp_path / "r.pdf").read_bytes() == b"old"
    assert (tmp_path / "r-1.pdf").read_bytes() == b"older"
    assert (tmp_path / "r-2.pdf").read_bytes() == b"new"


# ---------- save_attachments() against a fake server ----------


class FakeIMAP:
    messages: dict[bytes, bytes] = {}
    uidvalidity = b"1700000000"

    def __init__(self, host, port=993):
        pass

    def login(self, user, password):
        return ("OK", [b"ok"])

    def logout(self):
        return ("BYE", [b""])

    def select(self, mailbox, readonly=False):
        assert readonly
        return ("OK", [b"1"])

    def response(self, code):
        return (code, [self.uidvalidity])

    def uid(self, command, *args):
        assert command == "FETCH"
        raw = self.messages.get(args[0])
        if raw is None:
            return ("OK", [None])
        return ("OK", [(b"1 (UID 1 RFC822 {N}", raw), b")"])


@pytest.fixture
def server(monkeypatch):
    FakeIMAP.messages = {b"1": TWO, b"2": b"Subject: plain\r\n\r\nno attachments"}
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    return FakeIMAP


CONFIG = {"HOST": "h", "USER": "u", "PASS": "p"}


def test_save_all(server, tmp_path):
    res = client.save_attachments(CONFIG, ["1"], str(tmp_path))
    assert res[0]["status"] == "saved"
    assert [s["path"] for s in res[0]["saved"]] == [
        str(tmp_path / "report.pdf"),
        str(tmp_path / "photo.png"),
    ]
    assert (tmp_path / "report.pdf").read_bytes() == PDF


def test_save_twice_keeps_both(server, tmp_path):
    client.save_attachments(CONFIG, ["1"], str(tmp_path), selectors=["1"])
    client.save_attachments(CONFIG, ["1"], str(tmp_path), selectors=["1"])
    assert sorted(os.listdir(tmp_path)) == ["report-1.pdf", "report.pdf"]


def test_save_partial(server, tmp_path):
    res = client.save_attachments(CONFIG, ["1"], str(tmp_path), selectors=["photo.png", "9"])
    assert res[0]["status"] == "partial"
    assert res[0]["unmatched"] == ["9"]
    assert os.listdir(tmp_path) == ["photo.png"]


def test_save_no_attachment_and_missing_uid(server, tmp_path):
    res = client.save_attachments(CONFIG, ["2", "3"], str(tmp_path))
    assert [r["status"] for r in res] == ["skipped", "skipped"]
    assert res[0]["reason"] == "no attachment to save"
    assert res[1]["reason"] == "no message with this UID"
    assert os.listdir(tmp_path) == []


def test_save_uidvalidity_mismatch(server, tmp_path):
    with pytest.raises(RuntimeError, match="renumbered"):
        client.save_attachments(CONFIG, ["1"], str(tmp_path), uidvalidity="42")
    assert os.listdir(tmp_path) == []


def test_save_not_a_directory(server, tmp_path):
    with pytest.raises(RuntimeError, match="not a directory"):
        client.save_attachments(CONFIG, ["1"], str(tmp_path / "nope"))


def test_save_to_stdout(server):
    buf = io.BytesIO()
    res = client.save_attachments(CONFIG, ["1"], "-", selectors=["report.pdf"], stdout=buf)
    assert buf.getvalue() == PDF
    assert res[0]["saved"] == [{"index": 1, "filename": "report.pdf", "path": "-"}]


def test_save_to_stdout_needs_exactly_one(server):
    buf = io.BytesIO()
    with pytest.raises(RuntimeError, match="exactly one attachment, 2 selected"):
        client.save_attachments(CONFIG, ["1"], "-", stdout=buf)
    assert buf.getvalue() == b""


def test_format_saves():
    out = format_saves(
        [
            {
                "uid": "1",
                "status": "partial",
                "from": "a",
                "subject": "s",
                "saved": [{"index": 1, "filename": "r.pdf", "path": "/d/r.pdf"}],
                "unmatched": ["9"],
            }
        ]
    )
    assert out == "partial: UID 1\n  a | s\n  1: /d/r.pdf\n  no such attachment: 9"


# ---------- CLI ----------


@pytest.fixture
def cli_env(monkeypatch, tmp_path, server):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("IMAPQUERY_HOST", "h")
    monkeypatch.setenv("IMAPQUERY_USER", "u")
    monkeypatch.setenv("IMAPQUERY_PASS", "p")
    return tmp_path


def test_cli_save(cli_env, capsys):
    out_dir = cli_env / "out"
    out_dir.mkdir()
    rc = cli.main(["--uid", "1", "--save-attachments", str(out_dir), "--attachment", "1"])
    assert rc == 0
    res = json.loads(capsys.readouterr().out)
    assert res[0]["saved"][0]["path"] == str(out_dir / "report.pdf")


def test_cli_save_partial_exits_2(cli_env, capsys):
    rc = cli.main(["--uid", "1", "--save-attachments", str(cli_env), "--attachment", "9"])
    assert rc == 2


def test_cli_save_to_stdout(cli_env, capsys, monkeypatch):
    buf = io.BytesIO()
    monkeypatch.setattr("sys.stdout", io.TextIOWrapper(buf))
    rc = cli.main(["--uid", "1", "--save-attachments", "-", "--attachment", "report.pdf"])
    assert rc == 0
    assert buf.getvalue() == PDF


@pytest.mark.parametrize(
    "argv, msg",
    [
        (["--save-attachments", "."], "requires --uid"),
        (["--uid", "1", "--attachment", "1"], "only applies to --save-attachments"),
        (["--uid", "1", "--save-attachments", ".", "--move-to", "X"], "cannot be combined"),
        (["--uid", "1", "--save-attachments", ".", "--dry-run"], "only applies to --move-to"),
        (["--uid", "1", "--uidvalidity", "5"], "--uidvalidity only applies"),
    ],
)
def test_cli_flag_errors(argv, msg, capsys):
    with pytest.raises(SystemExit):
        cli.main(argv)
    assert msg in capsys.readouterr().err
