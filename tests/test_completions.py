"""Tests for shell completion scripts.

The drift checks here are the load-bearing tests: every long flag
defined on the CLI parser must appear in every shell's completion
script. Without this, adding a new flag and forgetting to update one
of the three scripts would silently degrade completion.
"""

from __future__ import annotations

import argparse

import pytest

from muttlike_imap import cli
from muttlike_imap.completions import BASH, FISH, SCRIPTS, ZSH, get_completion


def _long_flags(parser: argparse.ArgumentParser) -> list[str]:
    """Every ``--foo`` long flag the parser knows about."""
    flags: list[str] = []
    for action in parser._actions:
        for opt in action.option_strings:
            if opt.startswith("--"):
                flags.append(opt)
    return flags


PARSER_FLAGS = _long_flags(cli.build_parser())


class TestDispatcher:
    def test_known_shell(self):
        assert get_completion("zsh") == ZSH
        assert get_completion("bash") == BASH
        assert get_completion("fish") == FISH

    def test_unknown_shell_raises(self):
        with pytest.raises(ValueError, match="unknown shell"):
            get_completion("powershell")

    def test_scripts_dict_keys(self):
        assert sorted(SCRIPTS) == ["bash", "fish", "zsh"]


class TestNoDrift:
    """Each shell script must mention every long flag the parser defines."""

    @pytest.mark.parametrize("flag", PARSER_FLAGS)
    def test_zsh_mentions_flag(self, flag):
        assert flag in ZSH, f"{flag} missing from the zsh completion"

    @pytest.mark.parametrize("flag", PARSER_FLAGS)
    def test_bash_mentions_flag(self, flag):
        assert flag in BASH, f"{flag} missing from the bash completion"

    @pytest.mark.parametrize("flag", PARSER_FLAGS)
    def test_fish_mentions_flag(self, flag):
        # Fish completion uses the bare flag name (no leading --) on the
        # `complete -l <name>` line, so check for that form.
        bare = flag.lstrip("-")
        assert f" -l {bare}" in FISH, f"{flag} missing from the fish completion"


class TestStructuralMarkers:
    """Each script should declare itself in the way its shell expects."""

    def test_zsh_starts_with_compdef(self):
        assert ZSH.startswith("#compdef muttlike-imap")

    def test_zsh_registers_via_compdef(self):
        # The trailing `compdef _muttlike-imap muttlike-imap` is what makes
        # eval-source work. Autoload-from-$fpath uses the #compdef directive
        # at the top instead; both paths leave the binding registered.
        assert "compdef _muttlike-imap muttlike-imap" in ZSH

    def test_zsh_does_not_self_invoke(self):
        # `_muttlike-imap "$@"` at the end of the file would call _arguments
        # outside a completion context when eval-sourced, which errors out
        # with "_arguments: can only be called from completion function".
        assert '_muttlike-imap "$@"' not in ZSH

    def test_zsh_does_not_define_pattern_helper(self):
        # The pattern-modifier helper was removed: zsh's shell-level handling
        # of `~` makes bare-tilde completion unreliable, and patterns are
        # typically passed as quoted strings anyway.
        assert "_muttlike-imap-pattern" not in ZSH

    def test_bash_registers_function(self):
        assert "complete -F _muttlike_imap muttlike-imap" in BASH

    def test_fish_uses_complete_command(self):
        assert "complete -c muttlike-imap" in FISH

    def test_zsh_advertises_choices(self):
        # The (true false) choice for --imap-tls is the smallest signal
        # that we're using zsh's tailored completion, not just option names.
        assert "(true false)" in ZSH

    def test_bash_advertises_choices(self):
        assert 'compgen -W "true false"' in BASH

    def test_fish_advertises_choices(self):
        assert "'true false'" in FISH


class TestCliIntegration:
    """``muttlike-imap --completion <shell>`` prints the expected script and exits."""

    @pytest.mark.parametrize("shell", ["zsh", "bash", "fish"])
    def test_prints_script(self, shell, capsys):
        rc = cli.main(["--completion", shell])
        assert rc == 0
        out = capsys.readouterr().out
        assert out == get_completion(shell)

    def test_unknown_shell_argparse_rejects(self, capsys):
        # argparse's choices= validation kicks in before we ever call
        # get_completion, so it exits non-zero with a usage error.
        with pytest.raises(SystemExit) as exc:
            cli.main(["--completion", "powershell"])
        assert exc.value.code != 0
        err = capsys.readouterr().err
        assert "invalid choice" in err

    def test_help_mentions_completion(self):
        out = cli.build_parser().format_help()
        assert "--completion" in out
