"""IMAP connection, search, and result fetching."""

from __future__ import annotations

import contextlib
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


def get_preview(msg: email.message.Message, max_chars: int | None = 300) -> str:
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


def _select(imap: imaplib.IMAP4, mailbox: str, readonly: bool = True) -> str:
    """SELECT ``mailbox`` and return its UIDVALIDITY ("" if not reported).

    UIDs are only meaningful together with the UIDVALIDITY under which they
    were read: if it changes, the server has renumbered the folder.
    """
    mb_name = mailbox if mailbox.isascii() else imap_utf7_encode(mailbox)
    typ, _ = imap.select(mb_name, readonly=readonly)
    if typ != "OK":
        imap.logout()
        raise RuntimeError(f"cannot select mailbox {mailbox!r}")
    _typ, data = imap.response("UIDVALIDITY")
    if data and data[0]:
        value = data[0]
        return value.decode() if isinstance(value, bytes) else str(value)
    return ""


def _uid_fetch(imap: imaplib.IMAP4, uid: bytes, what: str) -> tuple[bytes, bytes] | None:
    """``UID FETCH`` one message; return ``(prefix, literal)`` or None."""
    typ, msg_data = imap.uid("FETCH", uid, what)
    if typ != "OK" or not msg_data:
        return None
    for item in msg_data:
        if isinstance(item, tuple) and len(item) == 2:
            return item
    return None


def _parse_internaldate(blob: bytes) -> datetime | None:
    """Extract INTERNALDATE from the FETCH response prefix, if present."""
    m = _INTERNALDATE_RE.search(blob)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1).decode("ascii"), "%d-%b-%Y %H:%M:%S %z")
    except ValueError:
        return None


def _record_for(
    uid: bytes,
    msg: email.message.Message,
    include_body: bool = False,
    uidvalidity: str = "",
) -> dict[str, str]:
    record: dict[str, str] = {
        "uid": uid.decode(),
        "uidvalidity": uidvalidity,
        "from": decode_header(msg.get("From", "")),
        "to": decode_header(msg.get("To", "")),
        "cc": decode_header(msg.get("CC", "")),
        "subject": decode_header(msg.get("Subject", "")),
        "date": msg.get("Date", ""),
        "message_id": (msg.get("Message-ID", "") or "").strip(),
        "in_reply_to": " ".join((msg.get("In-Reply-To", "") or "").split()),
        "references": " ".join((msg.get("References", "") or "").split()),
        "preview": get_preview(msg),
    }
    if include_body:
        record["body"] = get_preview(msg, max_chars=None)
    return record


def fetch_by_uids(
    config: dict[str, str],
    uids: list[str],
    mailbox: str = "INBOX",
    include_body: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[dict[str, str]]:
    imap = imap_connect(config, timeout)
    uidvalidity = _select(imap, mailbox)
    results: list[dict[str, str]] = []
    for uid_str in uids:
        uid = uid_str.encode()
        item = _uid_fetch(imap, uid, "(INTERNALDATE RFC822)")
        if item is None:
            continue
        _prefix, body = item
        msg = email.message_from_bytes(body)
        results.append(_record_for(uid, msg, include_body=include_body, uidvalidity=uidvalidity))
    imap.logout()
    return results


def search(
    config: dict[str, str],
    pattern: str,
    limit: int,
    mailbox: str,
    me: str | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    include_body: bool = False,
) -> list[dict[str, str]]:
    if me is None:
        me = config.get("USER", "")
    imap = imap_connect(config, timeout)
    uidvalidity = _select(imap, mailbox)

    compiled = compile_pattern(pattern, fold_only=False, me=me)
    try:
        typ, data = imap.uid("SEARCH", "CHARSET", "UTF-8", compiled.criteria.encode("utf-8"))
    except imaplib.IMAP4.error:
        # Server rejected 8-bit literals in quoted strings; retry with
        # diacritics stripped from every search value.
        compiled = compile_pattern(pattern, fold_only=True, me=me)
        typ, data = imap.uid("SEARCH", "CHARSET", "UTF-8", compiled.criteria.encode("ascii"))

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
            item = _uid_fetch(imap, uid, fetch_atom)
            if item is None:
                continue
            prefix, body = item
            internaldate = _parse_internaldate(prefix) or datetime.now(timezone.utc)
            msg = email.message_from_bytes(body)
            if all(p(msg, internaldate) for p in compiled.predicates):
                results.append(
                    _record_for(uid, msg, include_body=include_body, uidvalidity=uidvalidity)
                )
                if len(results) >= limit:
                    break
        imap.logout()
        return results

    uids = all_uids[-limit:]
    uids.reverse()
    results = []
    for uid in uids:
        item = _uid_fetch(imap, uid, fetch_atom)
        if item is None:
            continue
        msg = email.message_from_bytes(item[1])
        results.append(_record_for(uid, msg, include_body=include_body, uidvalidity=uidvalidity))

    imap.logout()
    return results


# ---------- Moving messages ----------


def _quote_mailbox(name: str) -> str:
    """mUTF-7-encode (if needed) and quote a mailbox name for a command."""
    encoded = name if name.isascii() else imap_utf7_encode(name)
    return '"' + encoded.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _capabilities(imap: imaplib.IMAP4) -> set[str]:
    """Post-login capabilities (they may differ from the pre-login ones)."""
    typ, data = imap.capability()
    if typ != "OK" or not data or not data[0]:
        return {c.upper() for c in getattr(imap, "capabilities", ())}
    raw = data[0].decode() if isinstance(data[0], bytes) else str(data[0])
    return {c.upper() for c in raw.split()}


def _copyuid(imap: imaplib.IMAP4, uid: str) -> str:
    """New UID of ``uid`` from a ``[COPYUID v src dst]`` response code, if any."""
    _typ, data = imap.response("COPYUID")
    for item in data or []:
        if not item:
            continue
        text = item.decode() if isinstance(item, bytes) else str(item)
        parts = text.split()
        if len(parts) == 3:
            src, dst = parts[1].split(","), parts[2].split(",")
            if len(src) == len(dst) and uid in src:
                return dst[src.index(uid)]
    return ""


def _find_by_message_id(imap: imaplib.IMAP4, message_id: str, size: str) -> list[str]:
    """UIDs in the selected folder with this Message-ID and RFC822 size."""
    mid = message_id.replace("\\", "\\\\").replace('"', '\\"')
    typ, data = imap.uid("SEARCH", "HEADER", "Message-ID", f'"{mid}"')
    if typ != "OK" or not data or not data[0]:
        return []
    found = []
    for uid in data[0].split():
        typ, fdata = imap.uid("FETCH", uid, "(RFC822.SIZE)")
        blob = b" ".join(x if isinstance(x, bytes) else x[0] for x in fdata or [] if x)
        m = re.search(rb"RFC822\.SIZE (\d+)", blob)
        if m and m.group(1).decode() == size:
            found.append(uid.decode())
    return found


def _describe(imap: imaplib.IMAP4, uid: str) -> dict[str, str] | None:
    """Headers and size of one message in the selected folder, or None."""
    typ, data = imap.uid(
        "FETCH",
        uid,
        "(RFC822.SIZE BODY.PEEK[HEADER.FIELDS (MESSAGE-ID FROM SUBJECT DATE)])",
    )
    if typ != "OK" or not data:
        return None
    for item in data:
        if isinstance(item, tuple) and len(item) == 2:
            m = re.search(rb"RFC822\.SIZE (\d+)", item[0])
            hdr = email.message_from_bytes(item[1])
            return {
                "size": m.group(1).decode() if m else "",
                "message_id": (hdr.get("Message-ID", "") or "").strip(),
                "from": decode_header(hdr.get("From", "")),
                "subject": decode_header(hdr.get("Subject", "")),
                "date": hdr.get("Date", ""),
            }
    return None


def move_messages(
    config: dict[str, str],
    uids: list[str],
    destination: str,
    mailbox: str = "INBOX",
    uidvalidity: str = "",
    dry_run: bool = False,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[dict[str, str]]:
    """Move messages, identified by UID, from ``mailbox`` to ``destination``.

    Designed so that a message can never be lost:

    * the destination must already exist; ``uidvalidity``, if given, must
      match the source folder's, otherwise nothing is touched;
    * with the ``MOVE`` extension (RFC 6851), the server moves atomically;
    * otherwise the message is copied, the copy is located in the
      destination (by Message-ID and size), and only then is the original
      flagged ``\\Deleted`` and removed with ``UID EXPUNGE`` (UIDPLUS),
      which expunges that UID alone. Without UIDPLUS, or when the copy
      cannot be verified, the original is left untouched.

    Returns one record per UID with a ``status`` of ``moved``,
    ``would-move`` (dry run), ``copied`` (copy verified, original kept)
    or ``skipped``, plus a ``reason`` when relevant.
    """
    if mailbox == destination:
        raise RuntimeError("source and destination mailboxes are the same")
    imap = imap_connect(config, timeout)
    try:
        caps = _capabilities(imap)
        typ, _ = imap.status(_quote_mailbox(destination), "(UIDVALIDITY)")
        if typ != "OK":
            raise RuntimeError(f"destination mailbox {destination!r} does not exist")
        current = _select(imap, mailbox, readonly=dry_run)
        if uidvalidity and current and uidvalidity != current:
            raise RuntimeError(
                f"UIDVALIDITY of {mailbox!r} is {current}, not {uidvalidity}: "
                "the folder was renumbered, re-read the UIDs"
            )
        if not uidvalidity:
            uidvalidity = current
        method = "MOVE" if "MOVE" in caps else ("COPY+UIDPLUS" if "UIDPLUS" in caps else "COPY")

        results: list[dict[str, str]] = []
        for uid in uids:
            info = _describe(imap, uid)
            rec = {"uid": uid, "uidvalidity": uidvalidity, "mailbox": mailbox}
            rec["destination"] = destination
            if info is None:
                rec.update(status="skipped", reason="no message with this UID")
                results.append(rec)
                continue
            rec.update({k: info[k] for k in ("message_id", "from", "subject", "date")})
            rec["method"] = method
            if dry_run:
                rec["status"] = "would-move"
                results.append(rec)
                continue
            results.append(rec)
            if method == "MOVE":
                typ, _ = imap.uid("MOVE", uid, _quote_mailbox(destination))
                if typ != "OK":
                    rec.update(status="skipped", reason="server refused MOVE")
                    continue
                rec.update(status="moved", new_uid=_copyuid(imap, uid))
                continue
            # COPY, then verify before touching the original.
            typ, _ = imap.uid("COPY", uid, _quote_mailbox(destination))
            if typ != "OK":
                rec.update(status="skipped", reason="server refused COPY")
                continue
            new_uid = _copyuid(imap, uid)
            if not info["message_id"]:
                rec.update(
                    status="copied",
                    new_uid=new_uid,
                    reason="no Message-ID to verify the copy; original kept",
                )
                continue
            _select(imap, destination, readonly=True)
            found = _find_by_message_id(imap, info["message_id"], info["size"])
            if _select(imap, mailbox, readonly=False) != uidvalidity:
                raise RuntimeError(f"UIDVALIDITY of {mailbox!r} changed during the move")
            if not found or (new_uid and new_uid not in found):
                rec.update(status="skipped", reason="copy not found in destination")
                continue
            new_uid = new_uid or found[-1]
            if method != "COPY+UIDPLUS":
                rec.update(status="copied", new_uid=new_uid, reason="no UIDPLUS; original kept")
                continue
            typ, _ = imap.uid("STORE", uid, "+FLAGS.SILENT", "(\\Deleted)")
            if typ != "OK":
                rec.update(status="copied", new_uid=new_uid, reason="could not flag original")
                continue
            typ, _ = imap.uid("EXPUNGE", uid)
            if typ != "OK":
                rec.update(
                    status="copied",
                    new_uid=new_uid,
                    reason="UID EXPUNGE failed; original flagged \\Deleted",
                )
                continue
            rec.update(status="moved", new_uid=new_uid)
        return results
    finally:
        with contextlib.suppress(Exception):
            imap.logout()
