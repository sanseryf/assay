"""Hint helpers: closest matches, type names, value previews.

Every hint here answers "what should it have been?", because "invalid value" tells
the person reading the report nothing they did not already know.
"""

from __future__ import annotations

import difflib
import json
from collections.abc import Iterable
from typing import Any

FORMAT_EXAMPLES: dict[str, str] = {
    "date": "YYYY-MM-DD",
    "date-time": "2024-01-31T14:05:00Z",
    "time": "14:05:00",
    "duration": "P1Y2M3D",
    "email": "name@example.com",
    "idn-email": "náme@example.com",
    "uri": "https://example.com/path",
    "uri-reference": "/path?query=1",
    "uuid": "3f2504e0-4f89-11d3-9a0c-0305e82c3301",
    "ipv4": "192.0.2.10",
    "ipv6": "2001:db8::1",
    "hostname": "example.com",
    "regex": "^[A-Z]{2}$",
}

# JSON Schema type names, checked in this order because bool is an int in Python.
_JSON_TYPES: tuple[tuple[type, str], ...] = (
    (bool, "boolean"),
    (int, "integer"),
    (float, "number"),
    (str, "string"),
    (list, "array"),
    (dict, "object"),
    (type(None), "null"),
)


def json_type(value: Any) -> str:
    """The JSON type name of a Python value, as a schema would describe it."""
    for python_type, name in _JSON_TYPES:
        if isinstance(value, python_type):
            return name
    return type(value).__name__


def closest_match(value: str, choices: Iterable[str], *, cutoff: float = 0.7) -> str | None:
    """Return the choice closest to ``value``, or ``None`` when nothing is close.

    Case-insensitive so ``employee_only`` can suggest ``EMPLOYEE_ONLY``.
    """
    if not value:
        return None
    by_key = {choice.casefold(): choice for choice in choices}
    if not by_key:
        return None
    matches = difflib.get_close_matches(value.casefold(), list(by_key), n=1, cutoff=cutoff)
    return by_key[matches[0]] if matches else None


def preview(value: Any, *, limit: int = 60) -> str:
    """A single-line, length-capped rendering of a value, safe for a table cell."""
    if isinstance(value, str):
        text = value
    elif isinstance(value, dict | list):
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    text = text.replace("\r", "\\r").replace("\n", "\\n").replace("\t", "\\t")
    return text if len(text) <= limit else text[: limit - 1] + "…"


def join_choices(choices: Iterable[str], *, limit: int = 8) -> str:
    """Render an allowed-value set for a hint line, eliding the tail."""
    items = [str(choice) for choice in choices]
    if len(items) <= limit:
        return ", ".join(items)
    return ", ".join(items[:limit]) + f", … (+{len(items) - limit} more)"


def is_leading_zero_candidate(path: str) -> bool:
    """Fields where a number that should be a string usually means a dropped zero."""
    leaf = path.rsplit(".", 1)[-1].split("[")[0].casefold()
    return any(token in leaf for token in ("zip", "postal", "postcode", "phone", "ssn", "id"))
