"""Mutt-style pattern search over IMAP."""

from .client import (
    attachments,
    fetch_by_uids,
    list_mailboxes,
    move_messages,
    save_attachments,
    search,
    search_engine,
    select_attachments,
)
from .config import load_config
from .parser import CompiledPattern, compile_pattern, parse_pattern

__version__ = "1.2.0"

__all__ = [
    "CompiledPattern",
    "__version__",
    "attachments",
    "compile_pattern",
    "fetch_by_uids",
    "list_mailboxes",
    "load_config",
    "move_messages",
    "parse_pattern",
    "save_attachments",
    "search",
    "search_engine",
    "select_attachments",
]
