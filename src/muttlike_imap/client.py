"""IMAP connection, search, and result fetching."""

from __future__ import annotations

import email
import email.header
import imaplib
import re
import socket
from datetime import datetime, timezone

from .mailbox import imap_utf7_encode, parse_list_response
from .parser import compile_pattern

DEFAULT_TIMEOUT = 20

_INTERNALDATE_RE = re.compile(rb'INTERNALDATE "([^"]+)"')


def decode_header(value: str) -> str:
    if not value:
        return ""
    parts = email.header.decode_header(value)
    out: list[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            enc = charset or "utf-8"
            try:
                out.append(part.decode(enc, errors="replace"))
            except (LookupError, TypeError):
                out.append(part.decode("utf-8", errors="replace"))
        else:
            out.append(part)
    return "".join(out)


def get_preview(msg: email.message.Message, max_chars: int = 300) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition", ""))
            if part.get_content_type() == "text/plain" and "attachment" not in disp:
                try:
                    charset = part.get_content_charset() or "utf-8"
                    payload = part.get_payload(decode=True)
                    return payload.decode(charset, errors="replace")[:max_chars].strip()
                except Exception:
                    pass
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    charset = part.get_content_charset() or "utf-8"
                    html = part.get_payload(decode=True).decode(charset, errors="replace")
                    text = re.sub(r"<[^>]+>", " ", html)
                    return re.sub(r"\s+", " ", text)[:max_chars].strip()
                except Exception:
                    pass
        return ""
    try:
        charset = msg.get_content_charset() or "utf-8"
        return msg.get_payload(decode=True).decode(charset, errors="replace")[:max_chars].strip()
    except Exception:
        return ""


def imap_connect(config: dict[str, str], timeout: int = DEFAULT_TIMEOUT) -> imaplib.IMAP4:
    socket.setdefaulttimeout(timeout)
    host = config.get("HOST", "")
    port = int(config.get("PORT", 993))
    user = config.get("USER", "")
    password = config.get("PASS", "")
    use_tls = config.get("TLS", "true").lower() == "true"
    if not host:
        raise RuntimeError("IMAP host not configured (set IMAPQUERY_HOST or use --imap-host)")
    if not user:
        raise RuntimeError("IMAP user not configured (set IMAPQUERY_USER or use --imap-user)")
    if not password:
        raise RuntimeError(
            "IMAP password not configured (set IMAPQUERY_PASS, --imap-password-env, "
            "or put PASS=… in the config file)"
        )
    imap: imaplib.IMAP4 = imaplib.IMAP4_SSL(host, port) if use_tls else imaplib.IMAP4(host, port)
    imap.login(user, password)
    return imap


def list_mailboxes(config: dict[str, str], timeout: int = DEFAULT_TIMEOUT) -> list[str]:
    imap = imap_connect(config, timeout)
    typ, data = imap.list()
    imap.logout()
    if typ != "OK" or not data:
        return []
    return parse_list_response(data)


def _parse_internaldate(blob: bytes) -> datetime | None:
    """Extract INTERNALDATE from the FETCH response prefix, if present."""
    m = _INTERNALDATE_RE.search(blob)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1).decode("ascii"), "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        return None


def _record_for(uid: bytes, msg: email.message.Message) -> dict[str, str]:
    return {
        "uid": uid.decode(),
        "from": decode_header(msg.get("From", "")),
        "to": decode_header(msg.get("To", "")),
        "cc": decode_header(msg.get("CC", "")),
        "subject": decode_header(msg.get("Subject", "")),
        "date": msg.get("Date", ""),
        "preview": get_preview(msg),
    }


def search(
    config: dict[str, str],
    pattern: str,
    limit: int,
    mailbox: str,
    me: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[dict[str, str]]:
    if me is None:
        me = config.get("USER", "")
    imap = imap_connect(config, timeout)
    mb_name = mailbox if mailbox.isascii() else imap_utf7_encode(mailbox)
    typ, _ = imap.select(mb_name, readonly=True)
    if typ != "OK":
        imap.logout()
        raise RuntimeError(f"cannot select mailbox {mailbox!r}")

    compiled = compile_pattern(pattern, fold_only=False, me=me)
    try:
        typ, data = imap.search("UTF-8", compiled.criteria.encode("utf-8"))
    except imaplib.IMAP4.error:
        # Server rejected 8-bit literals in quoted strings; retry with
        # diacritics stripped from every search value.
        compiled = compile_pattern(pattern, fold_only=True, me=me)
        typ, data = imap.search("UTF-8", compiled.criteria.encode("ascii"))

    if typ != "OK" or not data[0]:
        imap.logout()
        return []

    all_uids = data[0].split()
    fetch_atom = "(INTERNALDATE RFC822)"

    if compiled.predicates:
        # Walk newest-first across the full candidate set, applying the
        # post-filter predicates. Stop once we've collected ``limit`` matches.
        results: list[dict[str, str]] = []
        for uid in reversed(all_uids):
            typ, msg_data = imap.fetch(uid, fetch_atom)
            if typ != "OK" or not msg_data or not msg_data[0]:
                continue
            prefix, body = msg_data[0]
            internaldate = _parse_internaldate(prefix) or datetime.now(timezone.utc)
            msg = email.message_from_bytes(body)
            if all(p(msg, internaldate) for p in compiled.predicates):
                results.append(_record_for(uid, msg))
                if len(results) >= limit:
                    break
        imap.logout()
        return results

    uids = all_uids[-limit:]
    uids.reverse()
    results = []
    for uid in uids:
        typ, msg_data = imap.fetch(uid, fetch_atom)
        if typ != "OK":
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        results.append(_record_for(uid, msg))

    imap.logout()
    return results
