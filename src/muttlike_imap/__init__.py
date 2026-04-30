"""Mutt-style pattern search over IMAP."""

from .client import list_mailboxes, search
from .config import load_config
from .parser import CompiledPattern, compile_pattern, parse_pattern

__version__ = "1.0.0"

__all__ = [
    "CompiledPattern",
    "__version__",
    "compile_pattern",
    "list_mailboxes",
    "load_config",
    "parse_pattern",
    "search",
]
