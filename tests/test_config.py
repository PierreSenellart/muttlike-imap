"""Config-loading tests."""

from __future__ import annotations

import pytest

from muttlike_imap import config as config_mod


@pytest.fixture
def isolated_env(monkeypatch, tmp_path):
    """Wipe env vars + redirect $HOME so default search paths land in tmp_path."""
    for k in list(__import__("os").environ.keys()):
        if k.startswith("IMAPQUERY_") or k.startswith("IMAP_") or k == "XDG_CONFIG_HOME":
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


class TestEnvFile:
    def test_reads_canonical_keys(self, isolated_env):
        cfg = isolated_env / ".config" / "muttlike-imap" / "config"
        cfg.parent.mkdir(parents=True)
        cfg.write_text(
            "IMAP_HOST=imap.example.com\n"
            "IMAP_PORT=993\n"
            "IMAP_USER=me\n"
            "IMAP_PASS=secret\n"
            "IMAP_TLS=true\n"
        )
        out = config_mod.load_config()
        assert out["HOST"] == "imap.example.com"
        assert out["PORT"] == "993"
        assert out["USER"] == "me"
        assert out["PASS"] == "secret"
        assert out["TLS"] == "true"

    def test_accepts_unprefixed_keys(self, isolated_env):
        cfg = isolated_env / ".config" / "muttlike-imap" / "config"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("HOST=h\nUSER=u\nPASS=p\n")
        out = config_mod.load_config()
        assert out["HOST"] == "h"

    def test_accepts_imapquery_prefix(self, isolated_env):
        cfg = isolated_env / ".config" / "muttlike-imap" / "config"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("IMAPQUERY_HOST=h\nIMAPQUERY_USER=u\n")
        out = config_mod.load_config()
        assert out["HOST"] == "h"

    def test_strips_paired_double_quotes(self, isolated_env):
        cfg = isolated_env / ".config" / "muttlike-imap" / "config"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('PASS="quoted secret"\n')
        out = config_mod.load_config()
        assert out["PASS"] == "quoted secret"

    def test_strips_paired_single_quotes(self, isolated_env):
        cfg = isolated_env / ".config" / "muttlike-imap" / "config"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("PASS='quoted secret'\n")
        out = config_mod.load_config()
        assert out["PASS"] == "quoted secret"

    def test_does_not_strip_unpaired_trailing_quote(self, isolated_env):
        # A shell command ending in a closing double quote (sed "...") must
        # not have that quote stripped; otherwise the shell sees unbalanced
        # quoting and the command breaks.
        cfg = isolated_env / ".config" / "muttlike-imap" / "config"
        cfg.parent.mkdir(parents=True)
        cfg.write_text('PASS_CMD=gpg -d foo | sed "s/x/y/"\n')
        out = config_mod.load_config()
        assert out["PASS_CMD"] == 'gpg -d foo | sed "s/x/y/"'

    def test_does_not_strip_unpaired_leading_quote(self, isolated_env):
        cfg = isolated_env / ".config" / "muttlike-imap" / "config"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("PASS_CMD=\"foo' bar\n")
        out = config_mod.load_config()
        assert out["PASS_CMD"] == "\"foo' bar"

    def test_only_strips_one_pair(self, isolated_env):
        # If you wrap a quoted value in another set, only the outer pair
        # is removed.
        cfg = isolated_env / ".config" / "muttlike-imap" / "config"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("PASS=\"'inner'\"\n")
        out = config_mod.load_config()
        assert out["PASS"] == "'inner'"

    def test_skips_comments_and_blanks(self, isolated_env):
        cfg = isolated_env / ".config" / "muttlike-imap" / "config"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("# comment\n\nHOST=h\n")
        out = config_mod.load_config()
        assert out["HOST"] == "h"

    def test_falls_back_to_legacy_path(self, isolated_env):
        legacy = isolated_env / ".config" / "imap-smtp-email" / ".env"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("IMAP_HOST=legacy.example.com\n")
        out = config_mod.load_config()
        assert out["HOST"] == "legacy.example.com"

    def test_primary_overrides_legacy(self, isolated_env):
        primary = isolated_env / ".config" / "muttlike-imap" / "config"
        primary.parent.mkdir(parents=True)
        primary.write_text("HOST=primary.example.com\n")
        legacy = isolated_env / ".config" / "imap-smtp-email" / ".env"
        legacy.parent.mkdir(parents=True)
        legacy.write_text("IMAP_HOST=legacy.example.com\n")
        out = config_mod.load_config()
        assert out["HOST"] == "primary.example.com"


class TestEnvVars:
    def test_imapquery_env_overrides_file(self, isolated_env, monkeypatch):
        cfg = isolated_env / ".config" / "muttlike-imap" / "config"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("HOST=from-file\n")
        monkeypatch.setenv("IMAPQUERY_HOST", "from-env")
        out = config_mod.load_config()
        assert out["HOST"] == "from-env"

    def test_bare_imap_env_does_not_leak(self, isolated_env, monkeypatch):
        # IMAP_HOST as an env var should NOT leak in (only IMAPQUERY_* env vars do).
        # Otherwise users with mutt/offlineimap env vars would be hijacked.
        monkeypatch.setenv("IMAP_HOST", "should-be-ignored")
        out = config_mod.load_config()
        assert "HOST" not in out


class TestOverrides:
    def test_overrides_win(self, isolated_env, monkeypatch):
        monkeypatch.setenv("IMAPQUERY_HOST", "from-env")
        out = config_mod.load_config({"host": "from-cli"})
        assert out["HOST"] == "from-cli"

    def test_none_values_in_overrides_skipped(self, isolated_env, monkeypatch):
        monkeypatch.setenv("IMAPQUERY_HOST", "from-env")
        out = config_mod.load_config({"host": None})
        assert out["HOST"] == "from-env"


class TestExplicitConfigPath:
    def test_explicit_path_via_env(self, isolated_env, monkeypatch, tmp_path):
        custom = tmp_path / "custom.conf"
        custom.write_text("HOST=custom-host\n")
        monkeypatch.setenv("IMAPQUERY_CONFIG", str(custom))
        out = config_mod.load_config()
        assert out["HOST"] == "custom-host"


class TestResolvePassword:
    def test_uses_password_env_when_set(self, monkeypatch):
        monkeypatch.setenv("MY_PASS", "from-env")
        assert config_mod.resolve_password({"PASS": "from-config"}, "MY_PASS") == "from-env"

    def test_falls_back_to_config(self):
        assert config_mod.resolve_password({"PASS": "from-config"}) == "from-config"

    def test_returns_empty_when_nothing_set(self):
        assert config_mod.resolve_password({}) == ""

    def test_password_env_missing_returns_empty(self, monkeypatch):
        monkeypatch.delenv("UNSET_VAR", raising=False)
        assert config_mod.resolve_password({"PASS": "fallback"}, "UNSET_VAR") == ""

    def test_password_cmd_arg_runs_command(self):
        # printf is portable; first line of stdout becomes the password.
        assert (
            config_mod.resolve_password(
                {"PASS": "ignored"}, password_cmd="printf 'secret123\\nmetadata'"
            )
            == "secret123"
        )

    def test_password_cmd_arg_strips_trailing_newline(self):
        assert (
            config_mod.resolve_password({}, password_cmd="printf 'just-a-pass\\n'") == "just-a-pass"
        )

    def test_password_cmd_arg_wins_over_env_and_config(self, monkeypatch):
        monkeypatch.setenv("MY_PASS", "from-env")
        out = config_mod.resolve_password(
            {"PASS": "from-config"}, password_env="MY_PASS", password_cmd="printf 'from-cmd'"
        )
        assert out == "from-cmd"

    def test_pass_cmd_in_config_used_when_no_arg(self):
        out = config_mod.resolve_password({"PASS_CMD": "printf 'from-config-cmd'"})
        assert out == "from-config-cmd"

    def test_pass_cmd_in_config_preferred_over_pass(self):
        # When both are set in the config, the cmd form wins (more secure).
        out = config_mod.resolve_password({"PASS": "plain", "PASS_CMD": "printf 'from-cmd'"})
        assert out == "from-cmd"

    def test_password_env_arg_wins_over_pass_cmd_in_config(self, monkeypatch):
        monkeypatch.setenv("MY_PASS", "from-env")
        out = config_mod.resolve_password(
            {"PASS_CMD": "printf 'from-config-cmd'"}, password_env="MY_PASS"
        )
        assert out == "from-env"

    def test_password_cmd_failure_raises(self):
        with pytest.raises(RuntimeError, match="exited with status"):
            config_mod.resolve_password({}, password_cmd="false")

    def test_password_cmd_not_found_raises(self):
        with pytest.raises(RuntimeError, match="status"):
            config_mod.resolve_password({}, password_cmd="this-cmd-does-not-exist-xyz123")

    def test_password_cmd_empty_output_raises(self):
        with pytest.raises(RuntimeError, match="empty output"):
            config_mod.resolve_password({}, password_cmd="true")

    def test_password_cmd_timeout(self, monkeypatch):
        # Force a tiny timeout so the test is fast.
        monkeypatch.setattr(config_mod, "PASSWORD_CMD_TIMEOUT", 1)
        with pytest.raises(RuntimeError, match="timed out"):
            config_mod.resolve_password({}, password_cmd="sleep 5")


class TestPassCmdInFile:
    def test_imap_pass_cmd_loaded_from_file(self, isolated_env):
        cfg = isolated_env / ".config" / "muttlike-imap" / "config"
        cfg.parent.mkdir(parents=True)
        cfg.write_text("HOST=h\nUSER=u\nIMAP_PASS_CMD=pass email/imap\n")
        out = config_mod.load_config()
        assert out["PASS_CMD"] == "pass email/imap"
