"""The pipeline: one payload through the layers, a batch through the grouper.

A syntax failure stops later layers for *that payload only*; the batch keeps going.
Findings from every payload flow into one stream, which is then grouped, so the
report describes the batch rather than repeating itself per record.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .grouping import group_findings
from .loader import Payload
from .models import Finding, Layer, Report, Severity
from .schema import SchemaLayer
from .syntax import analyze

DEFAULT_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_DEPTH = 64


@dataclass(frozen=True, slots=True)
class Limits:
    """Guards against payloads that would exhaust memory or the stack."""

    max_bytes: int = DEFAULT_MAX_BYTES
    max_depth: int = DEFAULT_MAX_DEPTH


class Pipeline:
    """Run the implemented layers (syntax, schema) and group the findings."""

    def __init__(
        self,
        *,
        schema: dict[str, Any] | None = None,
        schema_id: str = "schema",
        limits: Limits | None = None,
    ) -> None:
        self.schema_layer = SchemaLayer(schema, schema_id=schema_id) if schema is not None else None
        self.limits = limits or Limits()

    def run(self, payloads: Iterable[Payload]) -> Report:
        """Check every payload, then group the batch."""
        started = time.perf_counter()
        findings: list[Finding] = []
        scanned = clean = 0

        for payload in payloads:
            scanned += 1
            payload_findings = self.check_one(payload)
            if payload_findings:
                findings.extend(payload_findings)
            else:
                clean += 1

        report = Report(
            payloads_scanned=scanned,
            payloads_clean=clean,
            groups=group_findings(findings),
            duration_ms=int((time.perf_counter() - started) * 1000),
        )
        return report

    def check_one(self, payload: Payload) -> list[Finding]:
        """Everything wrong with one payload, in layer order."""
        if payload.oversized:
            return [
                Finding(
                    layer=Layer.SYNTAX,
                    rule="input-too-large",
                    severity=Severity.ERROR,
                    path="$",
                    message=f"payload exceeds {self.limits.max_bytes:,} bytes",
                    hint="split the file into NDJSON records, or raise --max-bytes deliberately.",
                    payload_id=payload.payload_id,
                )
            ]

        result = analyze(payload.raw, payload.payload_id)
        findings = list(result.findings)
        if not result.ok:
            return findings

        depth = max_depth(result.document)
        if depth > self.limits.max_depth:
            findings.append(
                Finding(
                    layer=Layer.SYNTAX,
                    rule="input-too-deep",
                    severity=Severity.ERROR,
                    path="$",
                    message=f"nesting depth {depth} exceeds the limit of {self.limits.max_depth}",
                    hint="flatten the payload, or raise --max-depth deliberately.",
                    payload_id=payload.payload_id,
                )
            )
            return findings

        if self.schema_layer is not None:
            findings.extend(self.schema_layer.check(result.document, payload.payload_id))
        return findings


def max_depth(value: Any) -> int:
    """Nesting depth, counted iteratively so a deep payload cannot blow the stack."""
    deepest = 1
    stack: list[tuple[Any, int]] = [(value, 1)]
    while stack:
        node, depth = stack.pop()
        deepest = max(deepest, depth)
        if isinstance(node, dict):
            stack.extend((child, depth + 1) for child in node.values())
        elif isinstance(node, list):
            stack.extend((child, depth + 1) for child in node)
    return deepest


def check_payloads(
    payloads: Iterable[Payload],
    *,
    schema: dict[str, Any] | None = None,
    schema_id: str = "schema",
    limits: Limits | None = None,
) -> Report:
    """Convenience wrapper for programmatic use and tests."""
    return Pipeline(schema=schema, schema_id=schema_id, limits=limits).run(payloads)
