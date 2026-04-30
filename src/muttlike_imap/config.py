"""IMAP connection-config loading.

Search order, highest priority first:

1. Values explicitly passed in via CLI args (handled by ``cli.py``).
2. ``IMAPQUERY_*`` environment variables.
3. The file pointed at by ``$IMAPQUERY_CONFIG``, if set.
4. ``$XDG_CONFIG_HOME/muttlike-imap/config`` (or ``~/.config/muttlike-imap/config``).
5. ``~/.config/imap-smtp-email/.env``: kept as a fallback for setups
   inherited from the openclaw imap-smtp-email skill.

Files use ``KEY=VALUE`` lines. Keys are accepted with or without an
``IMAP_``/``IMAPQUERY_`` prefix: ``HOST``, ``PORT``, ``USER``, ``PASS``,
``PASS_CMD``, ``TLS``. ``PASS_CMD`` runs a shell command and uses the first
line of stdout as the password, avoiding plaintext on disk and the
environment-leak vectors of ``PASS``.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

CANONICAL_KEYS = ("HOST", "PORT", "USER", "PASS", "PASS_CMD", "TLS")
ACCEPTED_PREFIXES = ("IMAPQUERY_", "IMAP_", "")
PASSWORD_CMD_TIMEOUT = 10


def _canonicalize(key: str) -> str | None:
    key = key.strip().upper()
    for prefix in ACCEPTED_PREFIXES:
        if prefix and not key.startswith(prefix):
            continue
        bare = key[len(prefix) :]
        if bare in CANONICAL_KEYS:
            return bare
    return None


def _strip_paired_quotes(value: str) -> str:
    """Strip exactly one pair of matching quotes if both ends agree.

    Matches the conventional ``.env`` / ``dotenv`` parser semantics.
    Avoids corrupting shell commands like ``sed "..."`` whose trailing
    character happens to be a quote without a matching opener.
    """
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ('"', "'"):
        return value[1:-1]
    return value


def _read_env_file(path: Path) -> dict[str, str]:
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        canon = _canonicalize(k)
        if canon is not None:
            out[canon] = _strip_paired_quotes(v.strip())
    return out


def _config_search_paths() -> list[Path]:
    paths: list[Path] = []
    explicit = os.environ.get("IMAPQUERY_CONFIG")
    if explicit:
        paths.append(Path(explicit).expanduser())
    xdg = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".config"
    paths.append(base / "muttlike-imap" / "config")
    paths.append(Path.home() / ".config" / "imap-smtp-email" / ".env")
    return paths


def load_config(overrides: dict[str, str] | None = None) -> dict[str, str]:
    """Resolve the IMAP connection config.

    ``overrides`` (typically from CLI args) wins over everything else.
    Returns a dict with canonical keys: HOST, PORT, USER, PASS, TLS. Missing
    keys are simply absent: callers must handle defaults.
    """
    config: dict[str, str] = {}

    # Lowest priority first, then layer.
    for path in reversed(_config_search_paths()):
        config.update(_read_env_file(path))

    for key, value in os.environ.items():
        canon = _canonicalize(key)
        if canon is not None and key.startswith("IMAPQUERY_"):
            config[canon] = value

    if overrides:
        for k, v in overrides.items():
            if v is not None:
                config[k.upper()] = v

    return config


def _run_password_cmd(cmd: str) -> str:
    """Run a shell command and return the first line of stdout as the password.

    Run via ``/bin/sh -c`` so users can pipe (``gpg --decrypt foo | head -1``)
    or chain commands. Times out after ``PASSWORD_CMD_TIMEOUT`` seconds.
    """
    try:
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=PASSWORD_CMD_TIMEOUT,
            check=True,
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise RuntimeError(
            f"password command exited with status {e.returncode}"
            + (f": {stderr}" if stderr else "")
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"password command timed out after {PASSWORD_CMD_TIMEOUT}s") from e
    except FileNotFoundError as e:
        raise RuntimeError(f"password command not found: {e}") from e

    lines = result.stdout.splitlines()
    if not lines or not lines[0].strip():
        raise RuntimeError("password command produced empty output")
    return lines[0].rstrip("\r\n")


def resolve_password(
    config: dict[str, str],
    password_env: str | None = None,
    password_cmd: str | None = None,
) -> str:
    """Pick the IMAP password.

    Priority, highest first:

    1. ``--imap-password-cmd`` (CLI): run the given shell command.
    2. ``--imap-password-env`` (CLI): read the given environment variable.
    3. ``PASS_CMD`` from config: same as 1, but stored in the config file.
    4. ``PASS`` from config (or ``IMAPQUERY_PASS`` env var, which is folded
       into ``PASS`` during ``load_config``).

    Using ``PASS_CMD`` (or the ``--imap-password-cmd`` flag) keeps the
    password off disk and out of the process environment.
    Returns an empty string if nothing is set; the caller decides whether to fail.
    """
    if password_cmd:
        return _run_password_cmd(password_cmd)
    if password_env:
        return os.environ.get(password_env, "")
    if config.get("PASS_CMD"):
        return _run_password_cmd(config["PASS_CMD"])
    return config.get("PASS", "")
