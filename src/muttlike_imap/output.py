"""Output formatting for search results."""

from __future__ import annotations

import json
from collections.abc import Iterable


def format_summary(results: Iterable[dict[str, str]]) -> str:
    results = list(results)
    if not results:
        return "No results."
    out: list[str] = [f"{len(results)} result(s):", ""]
    for e in results:
        out.append(
            f"UID:{e.get('uid', '?')} | From:{e.get('from', '?')} | Date:{e.get('date', '?')}"
        )
        out.append(f"Subject:{e.get('subject', '?')}")
        body = (e.get("body") or "").strip()
        if body:
            out.append(f"Body:{body}")
        else:
            preview = (e.get("preview") or "").strip()[:300]
            if preview:
                out.append(f"Preview:{preview}")
        out.append("")
    return "\n".join(out).rstrip("\n")


def format_json(results: Iterable[dict[str, str]]) -> str:
    return json.dumps(list(results), ensure_ascii=False, indent=2)


def format_moves(results: Iterable[dict[str, str]]) -> str:
    results = list(results)
    if not results:
        return "Nothing to move."
    out: list[str] = []
    for e in results:
        line = f"{e.get('status', '?')}: UID {e.get('uid', '?')}"
        if e.get("new_uid"):
            line += f" -> {e.get('destination', '?')} UID {e['new_uid']}"
        else:
            line += f" -> {e.get('destination', '?')}"
        if e.get("method"):
            line += f" [{e['method']}]"
        out.append(line)
        if e.get("subject") or e.get("from"):
            out.append(f"  {e.get('from', '')} | {e.get('subject', '')}")
        if e.get("reason"):
            out.append(f"  reason: {e['reason']}")
    return "\n".join(out)
