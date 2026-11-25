"""The pipeline: one payload through the layers, a batch through the grouper.

A syntax failure stops later layers for *that payload only*; the batch keeps going.
Findings from every payload flow into one stream, which is then grouped, so the
report describes the batch rather than repeating itself per record.

Three layers run per payload (syntax, schema, setup, string). Setup rules that are
properties of the batch, such as constants, case drift, duplicates and type drift, observe
payloads as they pass and emit their findings at the end.
"""

from __future__ import annotations

import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .geo.checks import GeoLayer
from .geo.store import ZipStore
from .geo.verify import UspsVerifier
from .grouping import group_findings
from .loader import Payload
from .models import Finding, Layer, Report, Severity
from .redact import collect_values, redact_finding
from .rules import RulesConfig
from .schema import SchemaLayer
from .setup import SetupLayer
from .strings import check as check_strings
from .syntax import analyze

DEFAULT_MAX_BYTES = 32 * 1024 * 1024
DEFAULT_MAX_DEPTH = 64


@dataclass(frozen=True, slots=True)
class Limits:
    """Guards against payloads that would exhaust memory or the stack."""

    max_bytes: int = DEFAULT_MAX_BYTES
    max_depth: int = DEFAULT_MAX_DEPTH


@dataclass(slots=True)
class PayloadCheck:
    """What one payload produced, plus the document for batch-level rules."""

    findings: list[Finding] = field(default_factory=list)
    document: Any = None


class Pipeline:
    """Run the implemented layers and group the findings."""

    def __init__(
        self,
        *,
        schema: dict[str, Any] | None = None,
        schema_id: str = "schema",
        rules: RulesConfig | None = None,
        limits: Limits | None = None,
        geo_store: ZipStore | None = None,
        redact: bool = False,
    ) -> None:
        self.schema_layer = SchemaLayer(schema, schema_id=schema_id) if schema is not None else None
        self.rules = rules or RulesConfig()
        self.limits = limits or Limits()
        self.redact = redact
        self._values: set[str] = set()
        self._geo_store = geo_store
        self._verifier: UspsVerifier | None = None
        if self.rules.geo.active and self.rules.geo.verify.enabled:
            self._verifier = UspsVerifier.from_env(
                ttl_days=self.rules.geo.verify.cache_ttl_days,
                max_per_minute=self.rules.geo.verify.max_requests_per_minute,
            )
        self.geo_layer: GeoLayer | None = None
        if self.rules.geo.active:
            self.geo_layer = GeoLayer(self.rules.geo, geo_store, self._verifier)
            # Open the reference database now so a missing file fails at startup.
            self._geo_store = self.geo_layer.store

    def run(self, payloads: Iterable[Payload]) -> Report:
        """Check every payload, then group the batch."""
        started = time.perf_counter()
        setup = SetupLayer(self.rules)
        findings: list[Finding] = []
        scanned = clean = 0

        for payload in payloads:
            scanned += 1
            checked = self._check_one(payload, setup, self.geo_layer)
            if checked.findings:
                findings.extend(checked.findings)
            else:
                clean += 1
            if checked.document is not None:
                setup.observe(checked.document, payload.payload_id)

        batch: list[Finding] = list(setup.finalize())
        if self.geo_layer is not None:
            batch.extend(self.geo_layer.finalize())
        if self.redact:
            batch = [redact_finding(finding, self._values) for finding in batch]
        findings.extend(batch)
        return Report(
            payloads_scanned=scanned,
            payloads_clean=clean,
            groups=group_findings(findings),
            duration_ms=int((time.perf_counter() - started) * 1000),
            redacted=self.redact,
        )

    def check_one(self, payload: Payload) -> list[Finding]:
        """Everything wrong with one payload, ignoring batch-level rules."""
        setup = SetupLayer(self.rules)
        geo = (
            GeoLayer(self.rules.geo, self._geo_store, self._verifier)
            if self.rules.geo.active
            else None
        )
        return self._check_one(payload, setup, geo).findings

    def _check_one(self, payload: Payload, setup: SetupLayer, geo: GeoLayer | None) -> PayloadCheck:
        if payload.oversized:
            return PayloadCheck(
                [
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
            )

        result = analyze(payload.raw, payload.payload_id)
        findings = list(result.findings)
        if not result.ok:
            return PayloadCheck(findings)

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
            return PayloadCheck(findings)

        if self.schema_layer is not None:
            findings.extend(self.schema_layer.check(result.document, payload.payload_id))

        findings.extend(setup.duplicate_key_findings(payload.payload_id, result.duplicate_keys))
        findings.extend(setup.check(result.document, payload.payload_id))
        if self.rules.strings.enabled:
            findings.extend(
                check_strings(
                    result.document,
                    payload.payload_id,
                    max_length=self.rules.strings.max_length,
                )
            )
        if geo is not None:
            findings.extend(geo.check(result.document, payload.payload_id))
        if self.redact:
            values = collect_values(result.document)
            self._values.update(values)
            findings = [redact_finding(finding, values) for finding in findings]
        return PayloadCheck(findings, result.document)


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
    rules: RulesConfig | None = None,
    limits: Limits | None = None,
) -> Report:
    """Convenience wrapper for programmatic use and tests."""
    return Pipeline(schema=schema, schema_id=schema_id, rules=rules, limits=limits).run(payloads)
