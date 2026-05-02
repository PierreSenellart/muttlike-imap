"""CLI smoke tests: focused on argparse wiring and exit-code behavior."""

from __future__ import annotations

import imaplib

import pytest

from muttlike_imap import cli


@pytest.fixture
def fake_imap_class(monkeypatch):
    """Patch IMAP4_SSL with a fake. Tests can customize via the returned dict."""
    state: dict = {"search_response": ("OK", [b""]), "list_response": ("OK", []), "selected": None}

    class FakeIMAP:
        def __init__(self, host, port=993):
            self.host = host

        def login(self, user, password):
            return ("OK", [b"ok"])

        def logout(self):
            return ("BYE", [b""])

        def select(self, mailbox, readonly=False):
            state["selected"] = mailbox
            return ("OK", [b"1"])

        def search(self, charset, criteria):
            state["criteria"] = criteria
            return state["search_response"]

        def fetch(self, uid, what):
            return ("OK", [(b"x", b"From: a@x\r\nSubject: s\r\n\r\nbody")])

        def list(self, directory='""', pattern="*"):
            return state["list_response"]

    monkeypatch.setattr(imaplib, "IMAP4_SSL", FakeIMAP)
    return state


@pytest.fixture
def cli_env(monkeypatch, tmp_path):
    """Minimal config so the CLI can connect."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("IMAPQUERY_HOST", "h")
    monkeypatch.setenv("IMAPQUERY_USER", "u")
    monkeypatch.setenv("IMAPQUERY_PASS", "p")
    return tmp_path


class TestArgparse:
    def test_help(self, capsys):
        with pytest.raises(SystemExit) as exc:
            cli.build_parser().parse_args(["--help"])
        assert exc.value.code == 0
        out = capsys.readouterr().out
        assert "muttlike-imap" in out
        assert "--mailbox" in out

    def test_version(self, capsys):
        with pytest.raises(SystemExit):
            cli.build_parser().parse_args(["--version"])
        assert "muttlike-imap" in capsys.readouterr().out

    def test_default_pattern_is_ALL(self):
        ns = cli.build_parser().parse_args([])
        assert ns.pattern == "ALL"
        assert ns.limit == 10
        assert ns.mailbox == "INBOX"


class TestMain:
    def test_list_mailboxes(self, cli_env, fake_imap_class, capsys):
        fake_imap_class["list_response"] = (
            "OK",
            [b'(\\HasNoChildren) "/" "INBOX"', b'(\\HasNoChildren) "/" "Sent"'],
        )
        rc = cli.main(["--list-mailboxes"])
        assert rc == 0
        out = capsys.readouterr().out.splitlines()
        assert out == ["INBOX", "Sent"]

    def test_search_no_results_json(self, cli_env, fake_imap_class, capsys):
        rc = cli.main(["~U"])
        assert rc == 0
        # JSON empty array
        assert capsys.readouterr().out.strip() == "[]"

    def test_search_no_results_summary(self, cli_env, fake_imap_class, capsys):
        rc = cli.main(["~U", "--summary"])
        assert rc == 0
        assert "No results." in capsys.readouterr().out

    def test_imap_args_override_env(self, cli_env, fake_imap_class):
        captured = {}

        class Spy:
            def __init__(self, host, port=993):
                captured["host"] = host
                captured["port"] = port

            def login(self, u, p):
                return ("OK", [b""])

            def logout(self):
                return ("BYE", [b""])

            def list(self, directory='""', pattern="*"):
                return ("OK", [])

        import imaplib as _imaplib

        _imaplib.IMAP4_SSL = Spy
        cli.main(["--imap-host", "cli-host", "--imap-port", "1143", "--list-mailboxes"])
        assert captured["host"] == "cli-host"
        assert captured["port"] == 1143

    def test_password_env_arg(self, cli_env, fake_imap_class, monkeypatch):
        monkeypatch.setenv("MY_SECRET", "fromenv")
        monkeypatch.delenv("IMAPQUERY_PASS")  # ensure CLI flag is what supplies it
        rc = cli.main(["--imap-password-env", "MY_SECRET", "~U"])
        assert rc == 0

    def test_unknown_modifier_returns_error(self, cli_env, fake_imap_class, capsys):
        rc = cli.main(["~Z"])
        assert rc == 1
        err = capsys.readouterr().err
        assert "Error:" in err

    def test_missing_config_returns_error(self, monkeypatch, tmp_path, capsys):
        monkeypatch.setenv("HOME", str(tmp_path))
        for k in ("IMAPQUERY_HOST", "IMAPQUERY_USER", "IMAPQUERY_PASS"):
            monkeypatch.delenv(k, raising=False)
        rc = cli.main(["~U"])
        assert rc == 1
        assert "not configured" in capsys.readouterr().err

    def test_body_flag_adds_body_key(self, cli_env, fake_imap_class, capsys):
        fake_imap_class["search_response"] = ("OK", [b"1"])
        rc = cli.main(["~U", "--body"])
        assert rc == 0
        import json
        results = json.loads(capsys.readouterr().out)
        assert len(results) == 1
        assert "body" in results[0]

    def test_uid_flag_fetches_by_uid(self, cli_env, fake_imap_class, capsys):
        rc = cli.main(["--uid", "42", "--body"])
        assert rc == 0
        import json
        results = json.loads(capsys.readouterr().out)
        assert len(results) == 1
        assert results[0]["uid"] == "42"
        assert "body" in results[0]

    def test_uid_flag_multiple(self, cli_env, fake_imap_class, capsys):
        rc = cli.main(["--uid", "1", "2"])
        assert rc == 0
        import json
        results = json.loads(capsys.readouterr().out)
        assert len(results) == 2
