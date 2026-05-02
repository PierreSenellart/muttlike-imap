"""``muttlike-imap`` command-line entry point."""

from __future__ import annotations

import argparse
import socket
import sys

from . import __version__
from .client import DEFAULT_TIMEOUT, fetch_by_uids, list_mailboxes, search
from .completions import SCRIPTS, get_completion
from .config import load_config, resolve_password
from .output import format_json, format_summary


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="muttlike-imap",
        description="Search IMAP mailboxes using mutt-style patterns.",
    )
    p.add_argument(
        "pattern", nargs="?", default="ALL", help="Mutt-style search pattern. Default: ALL."
    )
    p.add_argument("--limit", type=int, default=10, help="Maximum number of results (default: 10).")
    p.add_argument("--mailbox", default="INBOX", help="IMAP folder to search (default: INBOX).")
    p.add_argument("--summary", action="store_true", help="Human-readable output instead of JSON.")
    p.add_argument("--body", action="store_true", help="Include full body text in output.")
    p.add_argument(
        "--uid",
        nargs="+",
        metavar="UID",
        help="Fetch specific messages by UID instead of searching.",
    )
    p.add_argument(
        "--list-mailboxes", action="store_true", help="List available IMAP folders and exit."
    )
    p.add_argument("--me", help="Email address used by ~p / ~P (defaults to IMAP_USER).")

    conn = p.add_argument_group("connection")
    conn.add_argument("--imap-host", help="IMAP server host.")
    conn.add_argument("--imap-port", type=int, help="IMAP server port (default 993).")
    conn.add_argument("--imap-user", help="IMAP username.")
    conn.add_argument(
        "--imap-password-env",
        help="Read the password from this environment variable instead of a file.",
    )
    conn.add_argument(
        "--imap-password-cmd",
        help=(
            "Run a shell command and use the first line of stdout as the password. "
            'Example: "pass email/imap.example.com". Avoids plaintext on disk and in env.'
        ),
    )
    conn.add_argument("--imap-tls", choices=("true", "false"), help="Use TLS (default true).")
    conn.add_argument("--config", help="Path to a config file (overrides default search).")
    conn.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT,
        help=f"Socket timeout in seconds (default {DEFAULT_TIMEOUT}).",
    )

    p.add_argument(
        "--completion",
        choices=sorted(SCRIPTS),
        metavar="SHELL",
        help=(
            "Print a shell completion script and exit. "
            'Install with e.g. `eval "$(muttlike-imap --completion zsh)"`.'
        ),
    )
    p.add_argument("--version", action="version", version=f"muttlike-imap {__version__}")
    return p


def _build_config(args: argparse.Namespace) -> dict[str, str]:
    import os

    if args.config:
        os.environ["IMAPQUERY_CONFIG"] = args.config

    overrides: dict[str, str] = {}
    if args.imap_host:
        overrides["HOST"] = args.imap_host
    if args.imap_port:
        overrides["PORT"] = str(args.imap_port)
    if args.imap_user:
        overrides["USER"] = args.imap_user
    if args.imap_tls:
        overrides["TLS"] = args.imap_tls

    config = load_config(overrides)
    pwd = resolve_password(config, args.imap_password_env, args.imap_password_cmd)
    if pwd:
        config["PASS"] = pwd
    return config


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.completion:
        sys.stdout.write(get_completion(args.completion))
        return 0
    try:
        config = _build_config(args)
        if args.list_mailboxes:
            for name in list_mailboxes(config, timeout=args.timeout):
                print(name)
            return 0
        me = args.me or config.get("USER", "")
        if args.uid:
            results = fetch_by_uids(
                config,
                args.uid,
                mailbox=args.mailbox,
                include_body=args.body,
                timeout=args.timeout,
            )
        else:
            results = search(
                config,
                args.pattern,
                limit=args.limit,
                mailbox=args.mailbox,
                me=me,
                timeout=args.timeout,
                include_body=args.body,
            )
        print(format_summary(results) if args.summary else format_json(results))
        return 0
    except socket.timeout:
        print(f"Error: timed out after {args.timeout}s", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
