"""Layer 4: text that is valid JSON but corrupted.

These payloads pass every schema check. ``"Smith\u200b"`` is a string; ``"JosÃ©"`` is
a string; 300 characters in a 40-character field is a string. The damage only shows
up when a downstream system rejects the record, or silently stores a duplicate name.

Findings here are mostly warnings: the payload is well-formed, just wrong.
"""

from __future__ import annotations

import json
import unicodedata
from collections.abc import Iterator
from typing import Any

import ftfy
from ftfy.badness import badness

from .jsonpath import resolve
from .models import Finding, Layer, Severity
from .paths import walk

ZERO_WIDTH = frozenset({"\u200b", "\u200c", "\u200d", "\ufeff", "\u2060"})
NON_BREAKING_SPACE = frozenset({"\u00a0", "\u202f"})
REPLACEMENT = "\ufffd"
_ALLOWED_CONTROL = frozenset({"\t", "\n", "\r"})
_FOREIGN_SCRIPTS = frozenset({"CYRILLIC", "GREEK"})


def check(
    document: Any,
    payload_id: str = "",
    *,
    max_length: dict[str, int] | None = None,
) -> list[Finding]:
    """Every string in the document, checked for corruption that passes a schema.

    ``max_length`` maps a JSONPath expression to a destination limit; schema
    ``maxLength`` already covers fields the schema describes, so this is for the
    limits a receiver imposes that the schema does not know about.
    """
    findings: list[Finding] = []
    for path, value in walk(document):
        if isinstance(value, str):
            findings.extend(_check_string(path, value, payload_id))
    for expression, limit in (max_length or {}).items():
        for path, value in resolve(document, expression):
            if isinstance(value, str) and len(value) > limit:
                findings.append(
                    Finding(
                        layer=Layer.STRING,
                        rule="too-long",
                        severity=Severity.WARNING,
                        path=path,
                        message=f"longer than {limit} characters",
                        hint="the destination limits this field; the value will be "
                        "truncated or rejected.",
                        value_preview=value,
                        payload_id=payload_id,
                    )
                )
    return findings


def _check_string(path: str, value: str, payload_id: str) -> list[Finding]:
    findings = [
        finding
        for finding in (
            _mojibake(path, value, payload_id),
            _zero_width(path, value, payload_id),
            _non_breaking_space(path, value, payload_id),
            _control_characters(path, value, payload_id),
            _whitespace(path, value, payload_id),
            _double_encoded(path, value, payload_id),
            _replacement_character(path, value, payload_id),
            _mixed_script(path, value, payload_id),
        )
        if finding is not None
    ]
    return findings


def _mojibake(path: str, value: str, payload_id: str) -> Finding | None:
    if not value or badness(value) == 0:
        return None
    repaired = ftfy.fix_text(value)
    if repaired == value:
        return None
    return Finding(
        layer=Layer.STRING,
        rule="mojibake",
        severity=Severity.WARNING,
        path=path,
        message="mojibake (UTF-8 decoded as cp1252)",
        hint=f"reads as {repaired!r}; re-export the source as UTF-8 (ftfy repairs this).",
        value_preview=value,
        payload_id=payload_id,
    )


def _zero_width(path: str, value: str, payload_id: str) -> Finding | None:
    char = next((item for item in value if item in ZERO_WIDTH), None)
    if char is None:
        return None
    return Finding(
        layer=Layer.STRING,
        rule="zero-width",
        severity=Severity.WARNING,
        path=path,
        message=f"zero-width character U+{ord(char):04X}",
        hint="strip it; it is invisible and breaks string equality, joins and dedupe.",
        value_preview=value,
        payload_id=payload_id,
    )


def _non_breaking_space(path: str, value: str, payload_id: str) -> Finding | None:
    char = next((item for item in value if item in NON_BREAKING_SPACE), None)
    if char is None:
        return None
    return Finding(
        layer=Layer.STRING,
        rule="non-breaking-space",
        severity=Severity.WARNING,
        path=path,
        message=f"non-breaking space U+{ord(char):04X}",
        hint="replace it with a regular space; it looks identical and fails equality.",
        value_preview=value,
        payload_id=payload_id,
    )


def _control_characters(path: str, value: str, payload_id: str) -> Finding | None:
    char = next(
        (
            item
            for item in value
            if unicodedata.category(item) == "Cc" and item not in _ALLOWED_CONTROL
        ),
        None,
    )
    if char is None:
        return None
    return Finding(
        layer=Layer.STRING,
        rule="control-character",
        severity=Severity.ERROR,
        path=path,
        message=f"control character U+{ord(char):04X}",
        hint="remove control characters; they truncate logs and corrupt downstream fields.",
        value_preview=value,
        payload_id=payload_id,
    )


def _whitespace(path: str, value: str, payload_id: str) -> Finding | None:
    if not value or value == value.strip():
        return None
    return Finding(
        layer=Layer.STRING,
        rule="whitespace",
        severity=Severity.WARNING,
        path=path,
        message="leading or trailing whitespace",
        hint="trim the value; whitespace breaks matching, joins and dedupe.",
        value_preview=value,
        payload_id=payload_id,
    )


def _double_encoded(path: str, value: str, payload_id: str) -> Finding | None:
    stripped = value.strip()
    if not stripped or stripped[0] not in "{[":
        return None
    try:
        decoded = json.loads(stripped)
    except (json.JSONDecodeError, RecursionError):
        return None
    if not isinstance(decoded, dict | list):
        return None
    return Finding(
        layer=Layer.STRING,
        rule="double-encoded",
        severity=Severity.ERROR,
        path=path,
        message="JSON encoded as a string",
        hint="parse the field upstream so the payload nests it as an object or array.",
        value_preview=value,
        payload_id=payload_id,
    )


def _replacement_character(path: str, value: str, payload_id: str) -> Finding | None:
    if REPLACEMENT not in value:
        return None
    return Finding(
        layer=Layer.STRING,
        rule="replacement-character",
        severity=Severity.WARNING,
        path=path,
        message="replacement character U+FFFD",
        hint="the original character was already lost; recover the value from the source.",
        value_preview=value,
        payload_id=payload_id,
    )


def _mixed_script(path: str, value: str, payload_id: str) -> Finding | None:
    token = next(_foreign_token(value), None)
    if token is None:
        return None
    return Finding(
        layer=Layer.STRING,
        rule="mixed-script",
        severity=Severity.WARNING,
        path=path,
        message="word mixes Latin with Cyrillic or Greek letters",
        hint="a lookalike character from another script; retype the name from the source.",
        value_preview=token,
        payload_id=payload_id,
    )


def _foreign_token(value: str) -> Iterator[str]:
    for token in value.split():
        scripts = {_script(char) for char in token if char.isalpha()}
        scripts.discard("")
        if "LATIN" in scripts and scripts & _FOREIGN_SCRIPTS:
            yield token


def _script(char: str) -> str:
    name = unicodedata.name(char, "")
    return name.split(" ", 1)[0] if " " in name else ""
