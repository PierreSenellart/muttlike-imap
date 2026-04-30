"""Output formatting tests."""

from __future__ import annotations

import json

from muttlike_imap.output import format_json, format_summary


def test_summary_empty():
    assert format_summary([]) == "No results."


def test_summary_basic():
    out = format_summary(
        [
            {"uid": "1", "from": "alice@x", "date": "Mon", "subject": "hi", "preview": "hello"},
        ]
    )
    assert "1 result(s):" in out
    assert "UID:1" in out
    assert "Subject:hi" in out
    assert "Preview:hello" in out


def test_summary_skips_preview_when_empty():
    out = format_summary(
        [
            {"uid": "1", "from": "alice@x", "date": "Mon", "subject": "hi", "preview": ""},
        ]
    )
    assert "Preview:" not in out


def test_summary_truncates_preview_at_300_chars():
    long = "x" * 1000
    out = format_summary([{"uid": "1", "subject": "s", "preview": long}])
    # Find the preview line and check length
    preview_line = next(line for line in out.splitlines() if line.startswith("Preview:"))
    assert len(preview_line) - len("Preview:") == 300


def test_summary_handles_missing_keys():
    # Missing fields fall back to "?"
    out = format_summary([{}])
    assert "UID:?" in out
    assert "Subject:?" in out


def test_json_round_trip():
    items = [{"uid": "1", "from": "alice@x", "subject": "hi"}]
    parsed = json.loads(format_json(items))
    assert parsed == items


def test_json_unicode_preserved():
    parsed = json.loads(format_json([{"subject": "Élément"}]))
    assert parsed[0]["subject"] == "Élément"
