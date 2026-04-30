"""Mutt-style pattern parser → IMAP search criteria string.

The grammar mirrors mutt's pattern-matching language as closely as IMAP allows:
``A B`` is AND (juxtaposition), ``A | B`` is OR, ``!A`` is NOT, and ``(...)``
groups. See ``docs/pattern-syntax.md`` for the full modifier reference.

Key limitations (vs mutt): IMAP SEARCH is substring-only (no regex);
mutt-runtime modifiers (~T/~v/~m/~n/~$/~#/PGP) have no IMAP equivalent and
raise ``ValueError``.
"""

from __future__ import annotations

import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass, field

from .dates import MessagePredicate, parse_daterange
from .sizes import parse_size_range


@dataclass
class CompiledPattern:
    """A parsed pattern split into an IMAP criteria string and zero or more
    Python predicates the client must apply post-fetch.

    Predicates only carry sub-day refinements from ``~d``/``~r`` modifiers
    that appear in a top-level conjunctive position. Modifiers inside
    ``OR`` alternatives, negations, or paren-grouped disjunctions fall back
    to day granularity (the IMAP server's resolution) because lifting their
    predicate to a top-level filter would change the pattern's meaning.
    """

    criteria: str
    predicates: list[MessagePredicate] = field(default_factory=list)


def ascii_fold(value: str) -> str:
    """Strip diacritics so names like Müller become Muller for IMAP search."""
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")


def quote(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def field_match(key: str, value: str, fold_only: bool = False) -> str:
    """Build a SUBJECT/FROM/etc. criterion.

    When the value has non-ASCII chars and ``fold_only`` is False, OR the
    original UTF-8 form with the ASCII-folded form so emails matching either
    are returned in one round trip. When the folded form is empty (e.g. CJK
    text), only the original form is used.
    """
    if fold_only:
        return f"{key} {quote(ascii_fold(value))}"
    folded = ascii_fold(value)
    if folded == value or not folded.strip():
        return f"{key} {quote(value)}"
    return f"OR {key} {quote(value)} {key} {quote(folded)}"


def header_match(name: str, value: str, fold_only: bool = False) -> str:
    """Generic ``HEADER name value`` with the same fan-out as field_match."""
    if fold_only:
        return f"HEADER {name} {quote(ascii_fold(value))}"
    folded = ascii_fold(value)
    if folded == value or not folded.strip():
        return f"HEADER {name} {quote(value)}"
    return f"OR HEADER {name} {quote(value)} HEADER {name} {quote(folded)}"


def L_match(value: str, fold_only: bool = False) -> str:
    """``~L`` matches From OR To OR CC, with both original and folded forms."""
    if fold_only:
        v = quote(ascii_fold(value))
        return f"OR (OR FROM {v} TO {v}) CC {v}"
    folded = ascii_fold(value)
    if folded == value or not folded.strip():
        v = quote(value)
        return f"OR (OR FROM {v} TO {v}) CC {v}"
    a, b = quote(value), quote(folded)
    return f"OR (OR (OR FROM {a} FROM {b}) (OR TO {a} TO {b})) (OR CC {a} CC {b})"


def C_match(value: str, fold_only: bool = False) -> str:
    """``~C`` matches To, Cc, or Bcc.

    Bcc is only useful in folders where you authored the message (your Sent
    folder), since the header is dropped from incoming mail by the sender's
    server. The criterion is harmless on regular folders.
    """
    if fold_only:
        v = quote(ascii_fold(value))
        return f"OR (OR TO {v} CC {v}) BCC {v}"
    folded = ascii_fold(value)
    if folded == value or not folded.strip():
        v = quote(value)
        return f"OR (OR TO {v} CC {v}) BCC {v}"
    a, b = quote(value), quote(folded)
    return f"OR (OR (OR TO {a} TO {b}) (OR CC {a} CC {b})) (OR BCC {a} BCC {b})"


def x_match(value: str, fold_only: bool = False) -> str:
    """``~x`` matches References OR In-Reply-To headers."""
    if fold_only:
        v = quote(ascii_fold(value))
        return f"OR HEADER References {v} HEADER In-Reply-To {v}"
    folded = ascii_fold(value)
    if folded == value or not folded.strip():
        v = quote(value)
        return f"OR HEADER References {v} HEADER In-Reply-To {v}"
    a, b = quote(value), quote(folded)
    return (
        f"OR (OR HEADER References {a} HEADER References {b}) "
        f"(OR HEADER In-Reply-To {a} HEADER In-Reply-To {b})"
    )


def parse_h_arg(value: str, fold_only: bool = False) -> str:
    """``~h`` takes ``Name: value``. Falls back to TEXT search if no colon."""
    if ":" not in value:
        return field_match("TEXT", value, fold_only)
    name, val = value.split(":", 1)
    return header_match(name.strip(), val.strip(), fold_only)


def build_or(parts: list[str]) -> str:
    """Build nested IMAP OR from a list of criteria strings."""
    if len(parts) == 1:
        return parts[0]
    return f"OR ({parts[0]}) ({build_or(parts[1:])})"


NO_ARG_FLAGS = {
    "A": "ALL",
    "U": "UNSEEN",
    "N": "UNSEEN",
    "R": "SEEN",
    "O": "SEEN",
    "F": "FLAGGED",
    "D": "DELETED",
    "Q": "ANSWERED",
}

# Mutt modifiers that have no IMAP equivalent. Raising on these is louder
# than silently returning ALL.
UNSUPPORTED_MODIFIERS = set("TvmnEHKMSXY#$gGklVuwY<>(")


class Parser:
    def __init__(self, s: str, fold_only: bool, me: str):
        self.s = s
        self.i = 0
        self.fold_only = fold_only
        self.me = me
        self.predicates: list[MessagePredicate] = []
        # ``top_level`` is True when emitted predicates can be applied as a
        # conjunctive top-level filter. We flip it False inside negations
        # and inside OR alternatives.
        self._top_level: bool = True

    def err(self, msg: str) -> None:
        raise ValueError(f"{msg} at position {self.i} in {self.s!r}")

    def skip_ws(self) -> None:
        while self.i < len(self.s) and self.s[self.i] in " \t":
            self.i += 1

    def peek(self) -> str | None:
        return self.s[self.i] if self.i < len(self.s) else None

    @contextmanager
    def _no_lift(self):
        saved = self._top_level
        self._top_level = False
        try:
            yield
        finally:
            self._top_level = saved

    def add_predicate(self, p: MessagePredicate | None) -> None:
        if p is not None and self._top_level:
            self.predicates.append(p)

    def parse(self) -> CompiledPattern:
        self.skip_ws()
        if self.i >= len(self.s):
            return CompiledPattern("ALL")
        result = self.parse_or()
        self.skip_ws()
        if self.i < len(self.s):
            self.err("unexpected character")
        return CompiledPattern(result, self.predicates)

    def parse_or(self) -> str:
        # Speculatively parse the first alternative at the current top_level.
        # If we discover a ``|`` that promotes this to a real disjunction,
        # any predicates collected from the first arm are no longer valid
        # at top level: roll them back, then parse the rest in non-top mode.
        saved_count = len(self.predicates)
        left = self.parse_and()
        self.skip_ws()
        if self.peek() == "|":
            del self.predicates[saved_count:]
            with self._no_lift():
                while self.peek() == "|":
                    self.i += 1
                    right = self.parse_and()
                    left = f"OR ({left}) ({right})"
                    self.skip_ws()
        return left

    def parse_and(self) -> str:
        parts: list[str] = []
        while True:
            self.skip_ws()
            ch = self.peek()
            if ch is None or ch == "|" or ch == ")":
                break
            parts.append(self.parse_atom())
        if not parts:
            return "ALL"
        if len(parts) == 1:
            return parts[0]
        return " ".join(parts)

    def parse_atom(self) -> str:
        self.skip_ws()
        negate = False
        if self.peek() == "!":
            negate = True
            self.i += 1
            self.skip_ws()

        def _inner() -> str:
            ch = self.peek()
            if ch == "(":
                self.i += 1
                inner = self.parse_or()
                self.skip_ws()
                if self.peek() != ")":
                    self.err("missing closing paren")
                self.i += 1
                return f"({inner})"
            if ch == "~":
                return self.parse_modifier()
            value = self.consume_token()
            if not value:
                self.err("expected pattern")
            return field_match("TEXT", value, self.fold_only)

        if negate:
            with self._no_lift():
                crit = _inner()
            return f"NOT ({crit})"
        return _inner()

    def consume_token(self) -> str:
        """Consume a whitespace/paren-delimited or quoted token."""
        self.skip_ws()
        if self.peek() == '"':
            self.i += 1
            start = self.i
            while self.i < len(self.s) and self.s[self.i] != '"':
                self.i += 1
            v = self.s[start : self.i]
            if self.i < len(self.s):
                self.i += 1
            return v
        start = self.i
        while self.i < len(self.s) and self.s[self.i] not in " \t()|":
            self.i += 1
        return self.s[start : self.i]

    def parse_modifier(self) -> str:
        self.i += 1  # consume ~
        if self.i >= len(self.s):
            self.err("expected modifier letter after ~")
        code = self.s[self.i]
        self.i += 1

        if code in NO_ARG_FLAGS:
            return NO_ARG_FLAGS[code]
        if code == "p":
            if not self.me:
                raise ValueError("~p requires a configured user (--me / IMAPQUERY_USER)")
            return C_match(self.me, self.fold_only)
        if code == "P":
            if not self.me:
                raise ValueError("~P requires a configured user (--me / IMAPQUERY_USER)")
            return field_match("FROM", self.me, self.fold_only)
        if code in ("d", "r"):
            value = self.consume_token()
            criteria, predicate = parse_daterange(value, code)
            self.add_predicate(predicate)
            return criteria
        if code == "z":
            return parse_size_range(self.consume_token())
        if code in "ftsbBcCLeiyhx":
            value = self.consume_token()
            if not value:
                self.err(f"~{code} requires an argument")
            return self.text_match(code, value)
        if code in UNSUPPORTED_MODIFIERS:
            raise ValueError(f"~{code} is a mutt-runtime modifier with no IMAP equivalent")
        raise ValueError(f"unknown modifier ~{code}")

    def text_match(self, code: str, value: str) -> str:
        if code == "f":
            return field_match("FROM", value, self.fold_only)
        if code == "t":
            return field_match("TO", value, self.fold_only)
        if code == "s":
            return field_match("SUBJECT", value, self.fold_only)
        if code == "b":
            return field_match("BODY", value, self.fold_only)
        if code == "B":
            return field_match("TEXT", value, self.fold_only)
        if code == "c":
            return field_match("CC", value, self.fold_only)
        if code == "C":
            return C_match(value, self.fold_only)
        if code == "L":
            return L_match(value, self.fold_only)
        if code == "e":
            return header_match("Sender", value, self.fold_only)
        if code == "i":
            return header_match("Message-ID", value, self.fold_only)
        if code == "y":
            return header_match("X-Label", value, self.fold_only)
        if code == "h":
            return parse_h_arg(value, self.fold_only)
        if code == "x":
            return x_match(value, self.fold_only)
        raise ValueError(f"unhandled text modifier ~{code}")


def compile_pattern(pattern_str: str, fold_only: bool = False, me: str = "") -> CompiledPattern:
    """Parse a mutt-style pattern into an IMAP criteria string + predicates."""
    return Parser(pattern_str, fold_only, me).parse()


def parse_pattern(pattern_str: str, fold_only: bool = False, me: str = "") -> str:
    """Parse a mutt-style pattern into an IMAP search criteria string.

    Convenience wrapper around :func:`compile_pattern` that drops the
    sub-day predicates. Use :func:`compile_pattern` if you need them.
    """
    return compile_pattern(pattern_str, fold_only, me).criteria
