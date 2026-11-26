"""Turn a report group into a ticket a team can act on.

The ticket is deliberately small: one group, one failing record, the expected value from
the schema, the actual value as a shape, and the fix hint. Values are redacted by default,
because tickets leave the machine and payloads usually carry names and addresses.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any

from .loader import Payload
from .models import Group
from .redact import shape

MISSING = object()


@dataclass(frozen=True, slots=True)
class Part:
    """One path component: a key, an array index, or an array wildcard."""

    kind: str
    value: str | int | None = None


@dataclass(frozen=True, slots=True)
class Ticket:
    """Everything needed to render a ticket in any target format."""

    fingerprint: str
    label: str
    summary: str
    path: str
    layer: str
    severity: str
    message: str
    hint: str | None
    count: int
    affected: int
    expected: str | None
    actual: str
    repro: Any

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "label": self.label,
            "summary": self.summary,
            "path": self.path,
            "layer": self.layer,
            "severity": self.severity,
            "message": self.message,
            "hint": self.hint,
            "count": self.count,
            "affected": self.affected,
            "expected": self.expected,
            "actual": self.actual,
            "repro": self.repro,
        }


def ticket_label(fingerprint: str) -> str:
    """A Jira-safe label derived from the fingerprint, stable across runs."""
    cleaned = re.sub(r"[^A-Za-z0-9_-]+", "-", "assay-" + fingerprint)
    cleaned = re.sub(r"-+", "-", cleaned).strip("-")
    return (cleaned or "assay")[:200]


def parse_path(path: str) -> list[Part]:
    """Parse ``members[3].zip`` into parts. ``$`` and a leading dot are ignored."""
    clean = path[1:] if path.startswith("$") else path
    parts: list[Part] = []
    index = 0
    while index < len(clean):
        if clean[index] == ".":
            index += 1
            continue
        if clean[index] == "[":
            end = clean.find("]", index)
            if end == -1:
                break
            token = clean[index + 1 : end]
            parts.append(Part("wildcard") if token == "*" else Part("index", int(token)))
            index = end + 1
            continue
        end = index
        while end < len(clean) and clean[end] not in ".[":
            end += 1
        parts.append(Part("key", clean[index:end]))
        index = end
    return parts


def _walk(value: Any, parts: list[Part]) -> Any:
    current = value
    for part in parts:
        if part.kind == "key":
            if not isinstance(current, dict) or part.value not in current:
                return MISSING
            current = current[part.value]
        elif part.kind == "wildcard":
            if not isinstance(current, list) or not current:
                return MISSING
            current = current[0]
        else:
            if not isinstance(current, list) or not isinstance(part.value, int):
                return MISSING
            if part.value >= len(current):
                return MISSING
            current = current[part.value]
    return current


def _skeleton(value: Any, parts: list[Part], *, redact: bool) -> Any:
    """A minimal object that keeps the path shape and only the failing leaf's value."""
    if not parts:
        if value is MISSING:
            return None
        return shape(value) if redact else value
    head, rest = parts[0], parts[1:]
    if head.kind == "key":
        child = value.get(head.value, MISSING) if isinstance(value, dict) else MISSING
        return {head.value: _skeleton(child, rest, redact=redact)}
    if head.kind == "wildcard":
        child = value[0] if isinstance(value, list) and value else MISSING
        return [_skeleton(child, rest, redact=redact)]
    if not isinstance(value, list) or not isinstance(head.value, int) or head.value >= len(value):
        child = MISSING
    else:
        child = value[head.value]
    return [_skeleton(child, rest, redact=redact)]


def _deref(node: Any, schema: dict[str, Any]) -> Any:
    """Follow local ``$ref`` pointers so expected values work with shared definitions."""
    while isinstance(node, dict) and "$ref" in node:
        reference = node["$ref"]
        if not isinstance(reference, str) or not reference.startswith("#/"):
            return node
        target: Any = schema
        for token in reference[2:].split("/"):
            if not isinstance(target, dict) or token not in target:
                return node
            target = target[token]
        node = target
    return node


def _describe(schema_node: Any) -> str:
    if not isinstance(schema_node, dict):
        return "a valid value"
    for key, prefix in (("const", "exactly "), ("enum", "one of ")):
        if key in schema_node:
            value = schema_node[key]
            return prefix + (json.dumps(value) if key == "const" else ", ".join(map(str, value)))
    pieces: list[str] = []
    node_type = schema_node.get("type")
    if isinstance(node_type, list):
        pieces.append(" or ".join(map(str, node_type)))
    elif node_type:
        pieces.append(str(node_type))
    if schema_node.get("format"):
        pieces.append("format " + str(schema_node["format"]))
    if schema_node.get("pattern"):
        pieces.append("matching " + str(schema_node["pattern"]))
    if schema_node.get("minLength") is not None:
        pieces.append("at least " + str(schema_node["minLength"]) + " characters")
    if schema_node.get("minimum") is not None:
        pieces.append("at least " + str(schema_node["minimum"]))
    return ", ".join(pieces) if pieces else "a valid value"


def expected_for(schema: dict[str, Any], path: str) -> str | None:
    """Walk the schema along a path and describe what the field should be."""
    node: Any = schema
    required_hint: str | None = None
    for part in parse_path(path):
        node = _deref(node, schema)
        if not isinstance(node, dict):
            return required_hint
        if part.kind == "key":
            properties = node.get("properties")
            if isinstance(properties, dict) and part.value in properties:
                node = properties[part.value]
            else:
                if isinstance(node.get("required"), list) and part.value in node["required"]:
                    required_hint = "required by the schema"
                return required_hint
        else:
            node = node.get("items")
    node = _deref(node, schema)
    return _describe(node)


def _payload_document(payload: Payload) -> Any:
    try:
        return json.loads(payload.raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return MISSING


def build_ticket(
    group: Group,
    payloads: list[Payload],
    schema: dict[str, Any],
    *,
    redact: bool = True,
) -> Ticket:
    """Build the minimal repro and ticket fields for one group."""
    occurrence = group.findings[0] if group.findings else None
    path = occurrence.path if occurrence and occurrence.path else group.path
    by_id = {payload.payload_id: payload for payload in payloads}
    payload = by_id.get(occurrence.payload_id) if occurrence else None
    if payload is None and payloads:
        payload = payloads[0]

    document = _payload_document(payload) if payload is not None else MISSING
    parts = parse_path(path)
    value = _walk(document, parts) if document is not MISSING else MISSING

    if document is MISSING:
        repro: Any = {}
        actual = "(payload could not be parsed)"
    else:
        repro = _skeleton(document, parts, redact=redact)
        if value is MISSING:
            actual = "(missing)"
        else:
            actual = shape(value) if redact else json.dumps(value, ensure_ascii=False)

    summary = f"[Assay] {path}: {group.message}"[:255]
    return Ticket(
        fingerprint=group.fingerprint,
        label=ticket_label(group.fingerprint),
        summary=summary,
        path=path,
        layer=group.layer.value,
        severity=group.severity.value,
        message=group.message,
        hint=group.hint,
        count=group.count,
        affected=group.payloads_affected,
        expected=expected_for(schema, path),
        actual=actual,
        repro=repro,
    )


def _plural(count: int, noun: str) -> str:
    return f"{count} {noun}" + ("" if count == 1 else "s")


def _rows(ticket: Ticket) -> list[tuple[str, str]]:
    return [
        ("Path", ticket.path),
        ("Layer", ticket.layer),
        ("Severity", ticket.severity),
        (
            "Affected",
            _plural(ticket.affected, "payload") + ", " + _plural(ticket.count, "finding"),
        ),
        ("Expected", ticket.expected or "see the message"),
        ("Actual", ticket.actual),
        ("Fix", ticket.hint or "See the path above."),
        ("Fingerprint", ticket.fingerprint),
        ("Label", ticket.label),
    ]


def render_markdown(ticket: Ticket) -> str:
    body = "\n".join(f"- {term}: {value}" for term, value in _rows(ticket))
    repro = json.dumps(ticket.repro, indent=2, ensure_ascii=False)
    return "\n".join(
        [
            f"### {ticket.message}",
            "",
            body,
            "",
            "**Minimal repro**",
            "```json",
            repro,
            "```",
            "",
            f"_Values are redacted. Label this issue `{ticket.label}`._",
        ]
    )


def render_jira(ticket: Ticket) -> str:
    body = "\n".join(f"*{term}:* {value}" for term, value in _rows(ticket))
    repro = json.dumps(ticket.repro, indent=2, ensure_ascii=False)
    return "\n".join(
        [
            f"h3. {ticket.message}",
            body,
            "",
            "{panel:title=Minimal repro}",
            "{code:json}",
            repro,
            "{code}",
            "{panel}",
            "",
            "Label this issue {{" + ticket.label + "}} so the next run finds it.",
        ]
    )


def _adf_text(text: str, *, strong: bool = False) -> dict[str, Any]:
    node: dict[str, Any] = {"type": "text", "text": text}
    if strong:
        node["marks"] = [{"type": "strong"}]
    return node


def render_adf(ticket: Ticket) -> dict[str, Any]:
    bullets = [
        {
            "type": "listItem",
            "content": [
                {
                    "type": "paragraph",
                    "content": [
                        _adf_text(term + ": ", strong=True),
                        _adf_text(value),
                    ],
                }
            ],
        }
        for term, value in _rows(ticket)
    ]
    return {
        "type": "doc",
        "version": 1,
        "content": [
            {
                "type": "heading",
                "attrs": {"level": 3},
                "content": [_adf_text(ticket.message)],
            },
            {"type": "bulletList", "content": bullets},
            {
                "type": "heading",
                "attrs": {"level": 4},
                "content": [_adf_text("Minimal repro")],
            },
            {
                "type": "codeBlock",
                "attrs": {"language": "json"},
                "content": [_adf_text(json.dumps(ticket.repro, indent=2, ensure_ascii=False))],
            },
        ],
    }


def render(ticket: Ticket, target: str) -> str:
    if target == "markdown":
        return render_markdown(ticket)
    if target == "jira":
        return render_jira(ticket)
    if target == "adf":
        return json.dumps(render_adf(ticket), indent=2, ensure_ascii=False)
    if target == "json":
        return json.dumps(ticket.to_dict(), indent=2, ensure_ascii=False)
    raise ValueError(f"unknown ticket format {target!r}")
