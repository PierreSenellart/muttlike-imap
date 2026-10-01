"""Tests for move_messages() against a stateful fake IMAP server.

The invariant checked throughout: whatever happens, every message that
existed before the call still exists in at least one folder afterwards.
"""

from __future__ import annotations

import imaplib
import json
import re

import pytest

from muttlike_imap import cli, client


def _raw(mid: str | None, subject: str = "s") -> bytes:
    head = f"From: alice@example.com\r\nSubject: {subject}\r\n"
    if mid:
        head += f"Message-ID: {mid}\r\n"
    return (head + "\r\nbody").encode()


class Folder:
    def __init__(self, uidvalidity: int, next_uid: int = 100):
        self.uidvalidity = uidvalidity
        self.next_uid = next_uid
        self.msgs: dict[int, dict] = {}

    def add(self, raw: bytes, flags: set[str] | None = None) -> int:
        uid = self.next_uid
        self.next_uid += 1
        self.msgs[uid] = {"raw": raw, "flags": set(flags or ())}
        return uid


class Server:
    """Shared state across connections; knobs let tests inject failures."""

    def __init__(self, caps=("IMAP4REV1", "MOVE", "UIDPLUS")):
        self.caps = set(caps)
        self.folders: dict[str, Folder] = {
            "INBOX": Folder(1000),
            "Archive": Folder(2000, next_uid=500),
        }
        self.refuse: set[str] = set()  # commands to answer NO
        self.copy_lands_nowhere = False  # COPY says OK but stores nothing
        self.renumber_inbox_after_copy = False
        self.commands: list[tuple] = []

    def all_raws(self) -> list[bytes]:
        return sorted(m["raw"] for f in self.folders.values() for m in f.msgs.values())


def _unquote(name: str) -> str:
    if name.startswith('"') and name.endswith('"'):
        return name[1:-1].replace('\\"', '"').replace("\\\\", "\\")
    return name


class FakeIMAP:
    def __init__(self, server: Server):
        self.s = server
        self.selected: str | None = None
        self.readonly = True
        self.untagged: dict[str, list] = {}

    # --- session ---
    def login(self, user, password):
        return ("OK", [b"ok"])

    def logout(self):
        return ("BYE", [b""])

    def capability(self):
        return ("OK", [" ".join(sorted(self.s.caps)).encode()])

    def response(self, code):
        return (code, self.untagged.pop(code, [None]))

    def status(self, mailbox, items):
        name = _unquote(mailbox)
        if name not in self.s.folders:
            return ("NO", [b"no such mailbox"])
        return ("OK", [f"{mailbox} (UIDVALIDITY {self.s.folders[name].uidvalidity})".encode()])

    def select(self, mailbox, readonly=False):
        name = _unquote(mailbox)
        if name not in self.s.folders:
            return ("NO", [b"no such mailbox"])
        self.selected, self.readonly = name, readonly
        self.untagged["UIDVALIDITY"] = [str(self.s.folders[name].uidvalidity).encode()]
        return ("OK", [str(len(self.s.folders[name].msgs)).encode()])

    # Sequence-number commands are never acceptable.
    def search(self, *a):
        raise AssertionError("sequence-number SEARCH used")

    def fetch(self, *a):
        raise AssertionError("sequence-number FETCH used")

    def expunge(self):
        raise AssertionError("plain EXPUNGE used; it would purge other \\Deleted messages")

    # --- UID commands ---
    def uid(self, command, *args):
        self.s.commands.append((command, self.selected) + args)
        if command in self.s.refuse:
            return ("NO", [b"refused"])
        folder = self.s.folders[self.selected]
        if command in ("MOVE", "COPY", "STORE", "EXPUNGE"):
            assert not self.readonly, f"{command} on a read-only selection"
        if command == "FETCH":
            uid, what = int(args[0]), args[1]
            m = folder.msgs.get(uid)
            if m is None:
                return ("OK", [None])
            if what == "(RFC822.SIZE)":
                return ("OK", [f"1 (UID {uid} RFC822.SIZE {len(m['raw'])})".encode()])
            head = m["raw"].split(b"\r\n\r\n")[0] + b"\r\n\r\n"
            prefix = f"1 (UID {uid} RFC822.SIZE {len(m['raw'])} BODY[HEADER] {{N}}".encode()
            return ("OK", [(prefix, head), b")"])
        if command == "SEARCH":
            assert args[0] == "HEADER"
            assert args[1] == "Message-ID"
            mid = args[2].strip('"')
            hits = [
                str(u) for u, m in folder.msgs.items() if f"Message-ID: {mid}".encode() in m["raw"]
            ]
            return ("OK", [" ".join(hits).encode()])
        if command in ("MOVE", "COPY"):
            uid, dest = int(args[0]), self.s.folders[_unquote(args[1])]
            m = folder.msgs.get(uid)
            if m is None:
                return ("OK", [None])
            if command == "COPY" and self.s.copy_lands_nowhere:
                return ("OK", [None])
            new = dest.add(m["raw"])
            if "UIDPLUS" in self.s.caps:
                self.untagged.setdefault("COPYUID", []).append(
                    f"{dest.uidvalidity} {uid} {new}".encode()
                )
            if command == "MOVE":
                del folder.msgs[uid]
            elif self.s.renumber_inbox_after_copy:
                folder.uidvalidity += 1
            return ("OK", [None])
        if command == "STORE":
            uid = int(args[0])
            assert args[1:] == ("+FLAGS.SILENT", "(\\Deleted)")
            folder.msgs[uid]["flags"].add("\\Deleted")
            return ("OK", [None])
        if command == "EXPUNGE":
            uid = int(args[0])
            if "\\Deleted" in folder.msgs[uid]["flags"]:
                del folder.msgs[uid]
            return ("OK", [None])
        raise AssertionError(f"unexpected UID command {command!r}")


@pytest.fixture
def server(monkeypatch):
    srv = Server()
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda host, port=993: FakeIMAP(srv))
    return srv


CONFIG = {"HOST": "h", "PORT": "993", "USER": "u", "PASS": "p", "TLS": "true"}


def _move(uids, dest="Archive", **kw):
    return client.move_messages(CONFIG, [str(u) for u in uids], dest, **kw)


class TestMoveExtension:
    def test_moves_atomically(self, server):
        uid = server.folders["INBOX"].add(_raw("<a@x>"))
        before = server.all_raws()
        [rec] = _move([uid])
        assert rec["status"] == "moved"
        assert rec["method"] == "MOVE"
        assert rec["new_uid"] == "500"
        assert uid not in server.folders["INBOX"].msgs
        assert server.all_raws() == before
        assert not any(c[0] == "COPY" for c in server.commands)

    def test_other_deleted_messages_untouched(self, server):
        # A message already flagged \Deleted must survive our move.
        other = server.folders["INBOX"].add(_raw("<other@x>"), flags={"\\Deleted"})
        uid = server.folders["INBOX"].add(_raw("<a@x>"))
        _move([uid])
        assert other in server.folders["INBOX"].msgs

    def test_refused_move_keeps_message(self, server):
        server.refuse.add("MOVE")
        uid = server.folders["INBOX"].add(_raw("<a@x>"))
        [rec] = _move([uid])
        assert rec["status"] == "skipped"
        assert uid in server.folders["INBOX"].msgs


class TestCopyFallback:
    def test_copy_verify_expunge_with_uidplus(self, server):
        server.caps = {"IMAP4REV1", "UIDPLUS"}
        other = server.folders["INBOX"].add(_raw("<other@x>"), flags={"\\Deleted"})
        uid = server.folders["INBOX"].add(_raw("<a@x>"))
        [rec] = _move([uid])
        assert rec["status"] == "moved"
        assert rec["method"] == "COPY+UIDPLUS"
        assert rec["new_uid"] == "500"
        assert uid not in server.folders["INBOX"].msgs
        assert other in server.folders["INBOX"].msgs  # UID EXPUNGE, not EXPUNGE
        assert len(server.folders["Archive"].msgs) == 1

    def test_without_uidplus_original_kept(self, server):
        server.caps = {"IMAP4REV1"}
        uid = server.folders["INBOX"].add(_raw("<a@x>"))
        [rec] = _move([uid])
        assert rec["status"] == "copied"
        assert "no UIDPLUS" in rec["reason"]
        assert uid in server.folders["INBOX"].msgs
        assert "\\Deleted" not in server.folders["INBOX"].msgs[uid]["flags"]
        assert rec["new_uid"] == "500"

    def test_unverifiable_copy_keeps_original(self, server):
        server.caps = {"IMAP4REV1", "UIDPLUS"}
        server.copy_lands_nowhere = True
        uid = server.folders["INBOX"].add(_raw("<a@x>"))
        [rec] = _move([uid])
        assert rec["status"] == "skipped"
        assert "not found" in rec["reason"]
        assert uid in server.folders["INBOX"].msgs
        assert "\\Deleted" not in server.folders["INBOX"].msgs[uid]["flags"]

    def test_no_message_id_keeps_original(self, server):
        server.caps = {"IMAP4REV1", "UIDPLUS"}
        uid = server.folders["INBOX"].add(_raw(None))
        [rec] = _move([uid])
        assert rec["status"] == "copied"
        assert uid in server.folders["INBOX"].msgs

    def test_refused_copy_keeps_original(self, server):
        server.caps = {"IMAP4REV1", "UIDPLUS"}
        server.refuse.add("COPY")
        uid = server.folders["INBOX"].add(_raw("<a@x>"))
        [rec] = _move([uid])
        assert rec["status"] == "skipped"
        assert uid in server.folders["INBOX"].msgs
        assert not server.folders["Archive"].msgs

    def test_failed_expunge_reported(self, server):
        server.caps = {"IMAP4REV1", "UIDPLUS"}
        server.refuse.add("EXPUNGE")
        uid = server.folders["INBOX"].add(_raw("<a@x>"))
        [rec] = _move([uid])
        assert rec["status"] == "copied"
        assert "EXPUNGE failed" in rec["reason"]
        assert len(server.folders["Archive"].msgs) == 1

    def test_renumbered_source_aborts_before_delete(self, server):
        server.caps = {"IMAP4REV1", "UIDPLUS"}
        server.renumber_inbox_after_copy = True
        uid = server.folders["INBOX"].add(_raw("<a@x>"))
        with pytest.raises(RuntimeError, match="changed during the move"):
            _move([uid])
        assert uid in server.folders["INBOX"].msgs
        assert not any(c[0] in ("STORE", "EXPUNGE") for c in server.commands)


class TestGuards:
    def test_missing_destination(self, server):
        uid = server.folders["INBOX"].add(_raw("<a@x>"))
        with pytest.raises(RuntimeError, match="does not exist"):
            _move([uid], dest="Nowhere")
        assert uid in server.folders["INBOX"].msgs
        assert server.commands == []

    def test_same_folder(self, server):
        with pytest.raises(RuntimeError, match="same"):
            _move([100], dest="INBOX")

    def test_uidvalidity_mismatch(self, server):
        uid = server.folders["INBOX"].add(_raw("<a@x>"))
        with pytest.raises(RuntimeError, match="renumbered"):
            _move([uid], uidvalidity="999")
        assert uid in server.folders["INBOX"].msgs
        assert server.commands == []

    def test_uidvalidity_match(self, server):
        uid = server.folders["INBOX"].add(_raw("<a@x>"))
        [rec] = _move([uid], uidvalidity="1000")
        assert rec["status"] == "moved"

    def test_unknown_uid_skipped(self, server):
        [rec] = _move([4242])
        assert rec["status"] == "skipped"
        assert "no message" in rec["reason"]

    def test_dry_run_changes_nothing(self, server):
        uid = server.folders["INBOX"].add(_raw("<a@x>", subject="hello"))
        [rec] = _move([uid], dry_run=True)
        assert rec["status"] == "would-move"
        assert rec["subject"] == "hello"
        assert uid in server.folders["INBOX"].msgs
        assert not server.folders["Archive"].msgs
        assert [c[0] for c in server.commands] == ["FETCH"]

    def test_destination_with_spaces_and_unicode_quoted(self, server):
        server.folders["Éléments envoyés"] = Folder(3000)
        server.folders["&AMk-l&AOk-ments envoy&AOk-s"] = server.folders.pop("Éléments envoyés")
        uid = server.folders["INBOX"].add(_raw("<a@x>"))
        [rec] = _move([uid], dest="Éléments envoyés")
        assert rec["status"] == "moved"
        move = next(c for c in server.commands if c[0] == "MOVE")
        assert move[3] == '"&AMk-l&AOk-ments envoy&AOk-s"'


class TestCli:
    @pytest.fixture
    def env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("HOME", str(tmp_path))
        monkeypatch.setenv("IMAPQUERY_HOST", "h")
        monkeypatch.setenv("IMAPQUERY_USER", "u")
        monkeypatch.setenv("IMAPQUERY_PASS", "p")

    def test_requires_uid(self, env, capsys):
        with pytest.raises(SystemExit):
            cli.main(["~f alice", "--move-to", "Archive"])
        assert "requires --uid" in capsys.readouterr().err

    def test_dry_run_requires_move_to(self, env, capsys):
        with pytest.raises(SystemExit):
            cli.main(["--uid", "1", "--dry-run"])

    def test_json_output_and_exit_code(self, env, server, capsys):
        uid = server.folders["INBOX"].add(_raw("<a@x>"))
        rc = cli.main(["--uid", str(uid), "--move-to", "Archive", "--uidvalidity", "1000"])
        assert rc == 0
        [rec] = json.loads(capsys.readouterr().out)
        assert rec["status"] == "moved"

    def test_partial_failure_exit_code(self, env, server, capsys):
        server.caps = {"IMAP4REV1"}
        uid = server.folders["INBOX"].add(_raw("<a@x>"))
        rc = cli.main(["--uid", str(uid), "--move-to", "Archive", "--summary"])
        assert rc == 2
        out = capsys.readouterr().out
        assert re.search(r"copied: UID \d+ -> Archive UID 500", out)
        assert "no UIDPLUS" in out

    def test_error_exit_code(self, env, server, capsys):
        rc = cli.main(["--uid", "1", "--move-to", "Nowhere"])
        assert rc == 1
        assert "does not exist" in capsys.readouterr().err
