"""External search engines (notmuch, or any command) over the maildirs behind IMAP.

The engine finds messages; IMAP then locates each one in its folder (from
the file path the engine reports) by Message-ID, so that the results carry
real UIDs, usable with ``--uid``, ``--move-to`` and ``--save-attachments``.

Configuration keys (see ``config.py``):

* ``SEARCH_CMD``: shell command running the engine, e.g. ``notmuch`` or
  ``ssh -q mailhost notmuch``. Arguments are appended to it, quoted; when
  it starts with ``ssh``, they are quoted a second time for the remote
  shell.
* ``SEARCH_ENGINE``: ``notmuch`` (default) or ``lines``. With ``lines``,
  ``SEARCH_CMD`` is given the query as its single argument and must print
  one ``<message-id><TAB><path>`` line per matching file, newest first.
* ``SEARCH_LAYOUT``: how folders are stored on disk, ``maildir++``
  (default: ``.Sub.Folder`` directories beside the INBOX's ``cur``/``new``)
  or ``fs`` (one directory per folder, nested, under ``SEARCH_ROOT``).
* ``SEARCH_ROOT``: with ``fs``, the directory of the INBOX (as the engine
  sees it).
"""

from __future__ import annotations

import json
import os
import posixpath
import shlex
import subprocess
from collections.abc import Iterator
from dataclasses import dataclass, field

from .mailbox import imap_utf7_decode

ENGINES = ("notmuch", "lines")
LAYOUTS = ("maildir++", "fs")


@dataclass
class Match:
    """A message found by the engine, with the files holding its copies."""

    message_id: str
    paths: list[str] = field(default_factory=list)


def command_line(cmd: str, args: list[str]) -> str:
    """``cmd`` followed by ``args``, quoted so that they reach the engine intact.

    ``ssh`` joins its arguments with spaces and has the remote shell parse
    the result again, so behind ``ssh`` each argument is quoted twice.
    """
    quoted = [shlex.quote(a) for a in args]
    try:
        first = shlex.split(cmd)[0]
    except (ValueError, IndexError):
        first = ""
    if os.path.basename(first) == "ssh":
        quoted = [shlex.quote(q) for q in quoted]
    return " ".join([cmd, *quoted])


def run(cmd: str, args: list[str], timeout: int) -> str:
    """Run the engine and return its standard output."""
    line = command_line(cmd, args)
    try:
        result = subprocess.run(
            line, shell=True, capture_output=True, text=True, timeout=timeout, check=True
        )
    except subprocess.CalledProcessError as e:
        stderr = (e.stderr or "").strip()
        raise RuntimeError(
            f"search command exited with status {e.returncode}"
            + (f": {stderr}" if stderr else "")
        ) from e
    except subprocess.TimeoutExpired as e:
        raise RuntimeError(f"search command timed out after {timeout}s") from e
    return result.stdout


def _notmuch_id(message_id: str) -> str:
    """A notmuch ``id:`` term for ``message_id``, quoted Xapian-style."""
    return 'id:"' + message_id.replace('"', '""') + '"'


def _shown_files(node: object, out: dict[str, list[str]]) -> None:
    """Collect ``id -> filenames`` from the nested output of ``notmuch show``."""
    if isinstance(node, list):
        for sub in node:
            _shown_files(sub, out)
    elif isinstance(node, dict) and "id" in node and "filename" in node:
        files = node["filename"]
        out[node["id"]] = files if isinstance(files, list) else [files]


def notmuch_matches(cmd: str, query: str, timeout: int, chunk: int) -> Iterator[Match]:
    """Messages matching ``query``, newest first, ``chunk`` per engine call pair."""
    offset = 0
    while True:
        out = run(
            cmd,
            [
                "search",
                "--format=json",
                "--output=messages",
                "--sort=newest-first",
                f"--offset={offset}",
                f"--limit={chunk}",
                query,
            ],
            timeout,
        )
        ids = json.loads(out or "[]")
        if not ids:
            return
        files: dict[str, list[str]] = {}
        shown = run(
            cmd,
            [
                "show",
                "--format=json",
                "--body=false",
                "--entire-thread=false",
                " or ".join(_notmuch_id(i) for i in ids),
            ],
            timeout,
        )
        _shown_files(json.loads(shown or "[]"), files)
        for i in ids:
            yield Match(i, files.get(i, []))
        if len(ids) < chunk:
            return
        offset += chunk


def lines_matches(cmd: str, query: str, timeout: int) -> Iterator[Match]:
    """Messages from ``<message-id><TAB><path>`` lines, in the order first seen."""
    found: dict[str, Match] = {}
    for line in run(cmd, [query], timeout).splitlines():
        mid, sep, path = line.partition("\t")
        mid = mid.strip().strip("<>")
        if not sep or not mid or not path:
            continue
        found.setdefault(mid, Match(mid)).paths.append(path)
    yield from found.values()


def matches(config: dict[str, str], query: str, timeout: int, chunk: int) -> Iterator[Match]:
    """Messages matching ``query`` according to the configured engine."""
    engine = config.get("SEARCH_ENGINE", "notmuch").lower()
    if engine not in ENGINES:
        raise RuntimeError(f"unknown SEARCH_ENGINE {engine!r}; choose one of {', '.join(ENGINES)}")
    cmd = config.get("SEARCH_CMD", "notmuch" if engine == "notmuch" else "")
    if not cmd:
        raise RuntimeError("SEARCH_CMD not configured (set it or use --search-cmd)")
    if engine == "notmuch":
        return notmuch_matches(cmd, query, timeout, chunk)
    return lines_matches(cmd, query, timeout)


def folder_for_path(path: str, delimiter: str, layout: str = "maildir++", root: str = "") -> str:
    """The IMAP folder holding the maildir file ``path``, or "" if it cannot tell.

    Folder names on disk are in modified UTF-7, like on the wire, and their
    components are separated by ``.`` (``maildir++``) or ``/`` (``fs``);
    they are decoded and joined with the server's ``delimiter``.
    """
    directory, sub = posixpath.split(posixpath.dirname(path))
    if sub not in ("cur", "new"):
        return ""
    if layout == "maildir++":
        name = posixpath.basename(directory)
        if name.startswith(".") and name not in (".", ".."):
            return delimiter.join(imap_utf7_decode(p) for p in name[1:].split("."))
        return "INBOX"
    if layout == "fs":
        if not root:
            raise RuntimeError("SEARCH_LAYOUT fs needs SEARCH_ROOT")
        rel = posixpath.relpath(directory, posixpath.normpath(root))
        if rel == ".":
            return "INBOX"
        if rel == ".." or rel.startswith("../"):
            return ""
        return delimiter.join(imap_utf7_decode(p) for p in rel.split("/"))
    raise RuntimeError(f"unknown SEARCH_LAYOUT {layout!r}; choose one of {', '.join(LAYOUTS)}")
