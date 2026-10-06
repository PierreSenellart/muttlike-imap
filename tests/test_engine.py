"""Tests for searching through an external engine (engine.py, client.search_engine)."""

from __future__ import annotations

import imaplib
import json
import re
import stat

import pytest

from muttlike_imap import cli, client, engine

# ---------- command lines ----------


def test_command_line_quotes_once_locally():
    assert engine.command_line("notmuch", ["search", "a b"]) == "notmuch search 'a b'"


def test_command_line_quotes_twice_behind_ssh():
    line = engine.command_line("ssh -q host notmuch", ["a b"])
    assert line == "ssh -q host notmuch ''\"'\"'a b'\"'\"''"


def test_run_passes_arguments_intact():
    out = engine.run("printf '%s|'", ["a b", "it's", '"q"', "$HOME"], timeout=5)
    assert out == "a b|it's|\"q\"|$HOME|"


def test_run_behind_ssh_passes_arguments_intact(tmp_path):
    # A stand-in for ssh: drop the host, join the arguments with spaces and
    # have a shell parse them again, as ssh and the remote shell do.
    fake = tmp_path / "ssh"
    fake.write_text('#!/bin/sh\nshift\nexec sh -c "$*"\n')
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    out = engine.run(f"{fake} host printf %s.", ["a b", "it's", "$HOME", "a;b"], timeout=5)
    assert out == "a b.it's.$HOME.a;b."


def test_run_failure_reports_stderr():
    with pytest.raises(RuntimeError, match="status 3: boom"):
        engine.run("sh -c 'echo boom >&2; exit 3' x", [], timeout=5)


# ---------- paths to folders ----------


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/home/u/Maildir/cur/123:2,S", "INBOX"),
        ("/home/u/Maildir/new/123", "INBOX"),
        ("/home/u/Maildir/.Archive/cur/123:2,S", "Archive"),
        ("/home/u/Maildir/.ml.gdr-im/cur/123:2,", "ml/gdr-im"),
        ("/home/u/Maildir/.&AMk-l&AOk-ments envoy&AOk-s/cur/1", "Éléments envoyés"),
        ("/home/u/Maildir/.Archive/tmp/123", ""),
        ("/home/u/notes.txt", ""),
    ],
)
def test_folder_for_path_maildirpp(path, expected):
    assert engine.folder_for_path(path, "/") == expected


@pytest.mark.parametrize(
    "path, expected",
    [
        ("/m/cur/1", "INBOX"),
        ("/m/Archive/cur/1", "Archive"),
        ("/m/ml/gdr-im/new/1", "ml.gdr-im"),
        ("/elsewhere/x/cur/1", ""),
    ],
)
def test_folder_for_path_fs(path, expected):
    assert engine.folder_for_path(path, ".", "fs", "/m/") == expected


def test_folder_for_path_fs_needs_root():
    with pytest.raises(RuntimeError, match="SEARCH_ROOT"):
        engine.folder_for_path("/m/cur/1", "/", "fs")


def test_folder_for_path_unknown_layout():
    with pytest.raises(RuntimeError, match="unknown SEARCH_LAYOUT"):
        engine.folder_for_path("/m/cur/1", "/", "mh")


# ---------- engines ----------


def test_shown_files_walks_nested_threads():
    shown = [
        [
            [
                {"id": "a@x", "filename": ["/m/cur/1", "/m/.A/cur/2"], "headers": {}},
                [[{"id": "b@x", "filename": "/m/cur/3"}, []]],
            ]
        ]
    ]
    files: dict[str, list[str]] = {}
    engine._shown_files(shown, files)
    assert files == {"a@x": ["/m/cur/1", "/m/.A/cur/2"], "b@x": ["/m/cur/3"]}


def test_notmuch_id_quoting():
    assert engine._notmuch_id('we"ird@x') == 'id:"we""ird@x"'


def test_notmuch_matches_pages_through_results(monkeypatch):
    ids = [f"m{i}@x" for i in range(5)]
    calls: list[list[str]] = []

    def fake_run(cmd, args, timeout):
        calls.append(args)
        if args[0] == "search":
            offset = int(args[4].split("=")[1])
            limit = int(args[5].split("=")[1])
            return json.dumps(ids[offset : offset + limit])
        wanted = re.findall(r'id:"([^"]+)"', args[-1])
        return json.dumps([[[{"id": i, "filename": [f"/m/cur/{i}"]}, []]] for i in wanted])

    monkeypatch.setattr(engine, "run", fake_run)
    found = list(engine.notmuch_matches("notmuch", "tag:x", 5, chunk=2))
    assert [m.message_id for m in found] == ids
    assert found[3].paths == ["/m/cur/m3@x"]
    searches = [c for c in calls if c[0] == "search"]
    assert [c[4] for c in searches] == ["--offset=0", "--offset=2", "--offset=4"]
    assert searches[0][-1] == "tag:x"


def test_notmuch_matches_is_lazy(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, args, timeout):
        calls.append(args)
        if args[0] == "search":
            return json.dumps(["a@x", "b@x"])
        return "[]"

    monkeypatch.setattr(engine, "run", fake_run)
    it = engine.notmuch_matches("notmuch", "q", 5, chunk=2)
    next(it)
    assert len(calls) == 2  # one search, one show: nothing fetched ahead


def test_lines_matches(monkeypatch):
    out = "<a@x>\t/m/cur/1\nb@x\t/m/.A/cur/2\na@x\t/m/.B/cur/3\ngarbage\n"
    monkeypatch.setattr(engine, "run", lambda cmd, args, timeout: out)
    found = list(engine.lines_matches("mysearch", "q", 5))
    assert [(m.message_id, m.paths) for m in found] == [
        ("a@x", ["/m/cur/1", "/m/.B/cur/3"]),
        ("b@x", ["/m/.A/cur/2"]),
    ]


def test_matches_config_errors():
    with pytest.raises(RuntimeError, match="unknown SEARCH_ENGINE"):
        engine.matches({"SEARCH_ENGINE": "mairix"}, "q", 5, 10)
    with pytest.raises(RuntimeError, match="SEARCH_CMD not configured"):
        engine.matches({"SEARCH_ENGINE": "lines"}, "q", 5, 10)


# ---------- search_engine() against a fake server ----------


def _raw(mid: str, sender: str = "alice@example.com") -> bytes:
    return f"From: {sender}\r\nSubject: s {mid}\r\nMessage-ID: <{mid}>\r\n\r\nbody".encode()


def _expand(uid_set: str) -> list[int]:
    out: list[int] = []
    for part in uid_set.split(","):
        lo, _, hi = part.partition(":")
        out += range(int(lo), int(hi or lo) + 1)
    return out


class FakeIMAP:
    folders: dict[str, dict[int, bytes]] = {}
    searches: list[tuple[str | None, str]] = []

    def __init__(self, host, port=993):
        self.selected: str | None = None

    def login(self, user, password):
        return ("OK", [b"ok"])

    def logout(self):
        return ("BYE", [b""])

    def list(self, directory='""', pattern="*"):
        assert (directory, pattern) == ('""', '""')
        return ("OK", [b'(\\Noselect) "/" ""'])

    def select(self, mailbox, readonly=False):
        assert readonly
        name = mailbox[1:-1] if mailbox.startswith('"') else mailbox
        if name not in self.folders:
            return ("NO", [b"no such folder"])
        self.selected = name
        return ("OK", [b"1"])

    def response(self, code):
        return (code, [b"77"])

    def uid(self, command, *args):
        msgs = self.folders[self.selected]
        if command == "SEARCH":
            criteria = args[2].decode()
            self.searches.append((self.selected, criteria))
            mid = re.search(r'HEADER Message-ID "([^"]*)"', criteria)
            sender = re.search(r'FROM "([^"]*)"', criteria)
            hits = [
                str(uid).encode()
                for uid, raw in msgs.items()
                if (mid is None or f"<{mid.group(1)}>".encode() in raw)
                and (sender is None or sender.group(1).encode() in raw)
            ]
            return ("OK", [b" ".join(hits)])
        if command == "FETCH" and "HEADER.FIELDS" in args[1]:
            out: list = []
            for uid in _expand(args[0]):
                if uid in msgs:
                    mid = re.search(rb"Message-ID: [^\r]*", msgs[uid]).group(0)
                    out += [(f"1 (UID {uid} BODY[HEADER.FIELDS (MESSAGE-ID)] {{N}}".encode(),
                             mid + b"\r\n\r\n"), b")"]
            return ("OK", out)
        if command == "FETCH":
            raw = msgs.get(int(args[0]))
            if raw is None:
                return ("OK", [None])
            return ("OK", [(b'1 (INTERNALDATE "01-Jan-2026 00:00:00 +0000" RFC822 {N}', raw)])
        raise AssertionError(command)


@pytest.fixture
def server(monkeypatch):
    FakeIMAP.folders = {
        "INBOX": {10: _raw("a@x"), 11: _raw("c@x", "bob@example.com")},
        "ml/gdr-im": {5: _raw("b@x")},
        "Archive": {7: _raw("a@x")},
    }
    FakeIMAP.searches = []
    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    return FakeIMAP


CONFIG = {"HOST": "h", "USER": "u", "PASS": "p", "SEARCH_ENGINE": "lines", "SEARCH_CMD": "x"}

LINES = "\n".join(
    [
        "b@x\t/M/.ml.gdr-im/cur/5",
        "a@x\t/M/cur/10",
        "a@x\t/M/.Archive/cur/7",
        "a@x\t/M/.Gone/cur/9",
        "c@x\t/M/new/11",
        "z@x\t/M/cur/404",
    ]
)


@pytest.fixture
def engine_output(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(cmd, args, timeout):
        calls.append(args)
        return LINES

    monkeypatch.setattr(engine, "run", fake_run)
    return calls


def test_search_engine_locates_each_copy(server, engine_output):
    res = client.search_engine(CONFIG, "anything", limit=10)
    assert [(r["mailbox"], r["uid"]) for r in res] == [
        ("ml/gdr-im", "5"),
        ("INBOX", "10"),
        ("Archive", "7"),
        ("INBOX", "11"),
    ]
    assert res[0]["uidvalidity"] == "77"
    assert res[0]["message_id"] == "<b@x>"
    assert engine_output == [["anything"]]


def test_search_engine_limit(server, engine_output):
    res = client.search_engine(CONFIG, "q", limit=2)
    assert [(r["mailbox"], r["uid"]) for r in res] == [("ml/gdr-im", "5"), ("INBOX", "10")]


def test_search_engine_pattern_filters_on_imap(server, engine_output):
    res = client.search_engine(CONFIG, "q", pattern="~f bob", limit=10)
    assert [(r["mailbox"], r["uid"]) for r in res] == [("INBOX", "11")]


def test_search_engine_pattern_searches_each_folder_once(server, monkeypatch):
    # Many engine matches in one folder: the pattern is searched once there,
    # not once per match.
    server.folders["INBOX"] = {i: _raw(f"m{i}@x") for i in range(1, 301)}
    server.folders["INBOX"][300] = _raw("m300@x", "bob@example.com")
    out = "\n".join(f"m{i}@x\t/M/cur/{i}" for i in range(300, 0, -1))
    monkeypatch.setattr(engine, "run", lambda cmd, args, timeout: out)
    res = client.search_engine(CONFIG, "q", pattern="~f bob", limit=10)
    assert [r["uid"] for r in res] == ["300"]
    assert len(server.searches) == 1
    assert "Message-ID" not in server.searches[0][1]


def test_uid_set():
    assert client._uid_set([b"9", b"3", b"4", b"5", b"11", b"10", b"4"]) == "3:5,9:11"


def test_search_engine_no_match(server, monkeypatch):
    monkeypatch.setattr(engine, "run", lambda cmd, args, timeout: "")
    assert client.search_engine(CONFIG, "q") == []


def test_cli_search(server, engine_output, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("HOME", str(tmp_path))
    for k, v in CONFIG.items():
        monkeypatch.setenv(f"IMAPQUERY_{k}", v)
    rc = cli.main(["--search", "from:alice", "--search-cmd", "other", "--limit", "1"])
    assert rc == 0
    res = json.loads(capsys.readouterr().out)
    assert [(r["mailbox"], r["uid"]) for r in res] == [("ml/gdr-im", "5")]


@pytest.mark.parametrize(
    "argv",
    [
        ["--search", "q", "--uid", "1"],
        ["--search", "q", "--mailbox", "Archive"],
        ["--search", "q", "--list-mailboxes"],
    ],
)
def test_cli_search_flag_errors(argv, capsys):
    with pytest.raises(SystemExit):
        cli.main(argv)
    assert "--search covers all folders" in capsys.readouterr().err
