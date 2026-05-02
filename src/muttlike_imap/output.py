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
