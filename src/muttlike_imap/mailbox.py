"""Mailbox name handling: modified UTF-7 (RFC 3501 §5.1.3) + LIST."""

from __future__ import annotations

import base64
import re

_LIST_RE = re.compile(r'\((?P<flags>[^)]*)\)\s+(?:"(?P<sep>[^"]*)"|NIL)\s+(?P<name>.+)')


def imap_utf7_encode(s: str) -> str:
    """Encode a string to IMAP modified UTF-7 for SELECT/LIST mailbox names."""
    out: list[str] = []
    buf: list[str] = []

    def flush() -> None:
        if not buf:
            return
        data = "".join(buf).encode("utf-16-be")
        b64 = base64.b64encode(data).decode("ascii").rstrip("=").replace("/", ",")
        out.append("&" + b64 + "-")
        buf.clear()

    for c in s:
        if c == "&":
            flush()
            out.append("&-")
        elif 0x20 <= ord(c) <= 0x7E:
            flush()
            out.append(c)
        else:
            buf.append(c)
    flush()
    return "".join(out)


def imap_utf7_decode(s: str) -> str:
    """Decode IMAP modified UTF-7 (RFC 3501 §5.1.3).

    Folder names like 'Éléments envoyés' come back as
    '&AMk-l&AOk-ments envoy&AOk-s' on servers that don't speak UTF8=ACCEPT.
    """
    out: list[str] = []
    i = 0
    while i < len(s):
        c = s[i]
        if c != "&":
            out.append(c)
            i += 1
            continue
        j = s.find("-", i + 1)
        if j == -1:
            out.append(s[i:])
            break
        chunk = s[i + 1 : j]
        if chunk == "":
            out.append("&")
        else:
            b64 = chunk.replace(",", "/")
            b64 += "=" * ((-len(b64)) % 4)
            try:
                out.append(base64.b64decode(b64).decode("utf-16-be"))
            except Exception:
                out.append(s[i : j + 1])
        i = j + 1
    return "".join(out)


def parse_list_response(items: list[bytes | None]) -> list[str]:
    """Parse the raw response of imap.list() into a list of decoded names.

    Skips ``\\Noselect`` containers.
    """
    mailboxes: list[str] = []
    for item in items:
        if item is None:
            continue
        line = item.decode("utf-8", errors="replace") if isinstance(item, bytes) else item
        m = _LIST_RE.match(line)
        if not m:
            continue
        flags = m.group("flags")
        name = m.group("name").strip()
        if name.startswith('"') and name.endswith('"'):
            name = name[1:-1]
        if "\\Noselect" in flags:
            continue
        mailboxes.append(imap_utf7_decode(name))
    return mailboxes
