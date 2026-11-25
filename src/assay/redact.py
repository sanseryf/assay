"""Redaction: shape tokens instead of values.

``--redact`` is for reports that leave the machine. Paths, types and signatures stay,
values do not: ``"John Smith"`` becomes ``"J*** S****"``, ``"75071"`` becomes
``"#####"``, and a date keeps its separators so the shape is still readable.

Grouping is unaffected because fingerprints never contained values in the first place.
Two hints are removed entirely rather than redacted, because they are *derived* from a
value and a substring scrub cannot know the derived form: the repaired text in a
mojibake hint and the standardized address in a USPS hint.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import replace
from typing import Any

from .models import Finding
from .paths import walk

_DATE = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}")
_SHAPE_HINTS = frozenset({"mojibake", "address-corrected"})
_MIN_SCRUB_LENGTH = 3


def shape(value: Any) -> str:
    """A stable shape token for one value."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, int | float):
        return "#" * len(str(value))
    text = str(value)
    if not text:
        return ""
    if _DATE.fullmatch(text) or text.replace("-", "").isdigit():
        return re.sub(r"\d", "#", text)
    return re.sub(r"(?<=\w)\w", "*", text)


def collect_values(document: Any) -> set[str]:
    """Every scalar in a payload, as text, long enough to scrub safely."""
    values: set[str] = set()
    for _, node in walk(document):
        if isinstance(node, str) and len(node) >= _MIN_SCRUB_LENGTH:
            values.add(node)
        elif isinstance(node, int | float) and not isinstance(node, bool):
            text = str(node)
            if len(text) >= _MIN_SCRUB_LENGTH:
                values.add(text)
    return values


def scrub_text(text: str, values: Iterable[str]) -> str:
    """Replace known payload values inside free text with their shapes."""
    for value in sorted(values, key=len, reverse=True):
        if len(value) < _MIN_SCRUB_LENGTH:
            continue
        text = text.replace(value, shape(value))
    return text


def redact_finding(finding: Finding, values: Iterable[str]) -> Finding:
    """A copy of the finding with previews shaped and values scrubbed from its text."""
    values = tuple(values)
    hint = finding.hint
    if finding.rule in _SHAPE_HINTS:
        hint = None
    elif hint:
        hint = scrub_text(hint, values)
    return replace(
        finding,
        message=scrub_text(finding.message, values),
        hint=hint,
        value_preview=shape(finding.value_preview) if finding.value_preview else None,
    )
