"""Shared JSON parsing helpers — defensive against malformed model output."""

from __future__ import annotations

import json
import re
from typing import Any, Dict


def parse_json_object(text: str) -> Dict[str, Any]:
    """Parse a JSON object from model output; return {} on failure (never raise)."""
    if not text or not str(text).strip():
        return {}
    text = str(text).strip()
    try:
        value = json.loads(text)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        pass

    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if fenced:
        try:
            value = json.loads(fenced.group(1))
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            pass

    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            value = json.loads(text[start : end + 1])
            return value if isinstance(value, dict) else {}
        except json.JSONDecodeError:
            repaired = _close_truncated_object(text[start:])
            if repaired:
                return repaired

    repaired = _close_truncated_object(text[text.find("{") :]) if "{" in text else {}
    return repaired if repaired else {}


def _close_truncated_object(fragment: str) -> Dict[str, Any]:
    """Best-effort close of truncated JSON objects/arrays. Never raises."""
    if not fragment or "{" not in fragment:
        return {}
    # Do not invent string tails — only close containers after a complete token.
    if fragment.count('"') % 2 == 1:
        fragment = fragment + '"'
    opens_curly = fragment.count("{") - fragment.count("}")
    opens_square = fragment.count("[") - fragment.count("]")
    if opens_curly < 0 or opens_square < 0:
        return {}
    candidate = fragment + ("]" * opens_square) + ("}" * opens_curly)
    try:
        value = json.loads(candidate)
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}
