"""Robust extraction of JSON from LLM text output."""

from __future__ import annotations

import json
import re
from typing import Any

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def parse_json(text: str | None) -> Any | None:
    """Parse JSON from model output that may be wrapped in code fences or prose.

    Returns None when no valid JSON object/array can be recovered.
    """
    if not text:
        return None
    text = text.strip()
    candidates = [text]
    candidates += [m.strip() for m in _FENCE.findall(text)]
    for opener, closer in (("{", "}"), ("[", "]")):
        start, end = text.find(opener), text.rfind(closer)
        if start != -1 and end > start:
            candidates.append(text[start : end + 1])
    for c in candidates:
        try:
            return json.loads(c)
        except (json.JSONDecodeError, ValueError):
            continue
    return None
