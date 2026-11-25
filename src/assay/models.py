"""Core data model.

A :class:`Finding` is one problem, pinned to one JSON path in one payload. Findings
are immutable and comparable by value so they can be deduplicated and hashed.

A :class:`Group` collapses findings that share a root cause: same layer, same rule,
same *shape* of path (``members[847].zip`` -> ``members[*].zip``) and the same error
signature. Grouping never looks at the value, which is why ``--redact`` cannot break
it.

A :class:`Report` is one batch result: what was scanned, what came back grouped, and
how long it took.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class Layer(StrEnum):
    """The pipeline layer that produced a finding, in execution order."""

    SYNTAX = "syntax"
    SCHEMA = "schema"
    SETUP = "setup"
    STRING = "string"
    GEO = "geo"

    @property
    def rank(self) -> int:
        """Lower is earlier; earlier layers outrank later ones on impact ties."""
        return _LAYER_RANK[self]


_LAYER_RANK = {
    Layer.SYNTAX: 0,
    Layer.SCHEMA: 1,
    Layer.SETUP: 2,
    Layer.STRING: 3,
    Layer.GEO: 4,
}


class Severity(StrEnum):
    """How much a finding should hurt. Weighted into a group's impact score."""

    ERROR = "error"
    WARNING = "warning"
    INFO = "info"

    @property
    def weight(self) -> float:
        return _SEVERITY_WEIGHT[self]

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_WEIGHT = {Severity.ERROR: 1.0, Severity.WARNING: 0.4, Severity.INFO: 0.1}
_SEVERITY_RANK = {Severity.ERROR: 0, Severity.WARNING: 1, Severity.INFO: 2}


def coerce_severity(value: str | Severity) -> Severity:
    """Accept ``"error"``/``"ERROR"`` from config files as well as enum members."""
    if isinstance(value, Severity):
        return value
    try:
        return Severity(str(value).strip().lower())
    except ValueError as exc:  # pragma: no cover - config validation covers this
        raise ValueError(f"unknown severity {value!r}; expected error, warning or info") from exc


@dataclass(frozen=True, slots=True)
class Finding:
    """One problem in one payload.

    ``path`` is a JSON path without the leading ``$`` (``members[3].dob``). ``line``
    and ``col`` are set only for problems in the payload text itself, which can only
    happen in the syntax layer.
    """

    layer: Layer
    rule: str
    severity: Severity
    path: str
    message: str
    signature: str = ""
    hint: str | None = None
    value_preview: str | None = None
    payload_id: str = ""
    line: int | None = None
    col: int | None = None

    @property
    def location(self) -> str:
        """Human-facing location: ``payload.json:12:5`` for syntax, else the path."""
        if self.line is not None:
            return f"{self.payload_id}:{self.line}:{self.col or 0}"
        return self.path


@dataclass(slots=True)
class Group:
    """Findings that share a root cause, with the numbers needed to rank them."""

    fingerprint: str
    layer: Layer
    rule: str
    path: str
    severity: Severity
    message: str
    findings: list[Finding] = field(default_factory=list)
    payload_ids: set[str] = field(default_factory=set)
    sample_values: list[str] = field(default_factory=list)
    impact: float = 0.0
    correlated_with: list[str] = field(default_factory=list)
    hint: str | None = None

    @property
    def count(self) -> int:
        """Total findings in this group (can exceed payloads_affected)."""
        return len(self.findings)

    @property
    def payloads_affected(self) -> int:
        """Distinct payloads with at least one finding in this group."""
        return len(self.payload_ids)

    @property
    def record_label(self) -> str:
        return "record" if self.payloads_affected == 1 else "records"

    def add(self, finding: Finding, max_samples: int = 3) -> None:
        """Add a finding, keeping at most ``max_samples`` distinct example values."""
        self.findings.append(finding)
        self.payload_ids.add(finding.payload_id)
        preview = finding.value_preview
        if preview and preview not in self.sample_values and len(self.sample_values) < max_samples:
            self.sample_values.append(preview)

    def recompute_impact(self) -> float:
        """Impact = affected payloads x severity weight, as specified in the design."""
        self.impact = self.payloads_affected * self.severity.weight
        return self.impact


@dataclass(slots=True)
class Report:
    """The result of one batch."""

    payloads_scanned: int = 0
    payloads_clean: int = 0
    groups: list[Group] = field(default_factory=list)
    duration_ms: int = 0

    @property
    def findings_total(self) -> int:
        return sum(g.count for g in self.groups)

    @property
    def payloads_failed(self) -> int:
        return self.payloads_scanned - self.payloads_clean

    @property
    def errors(self) -> int:
        return sum(g.count for g in self.groups if g.severity is Severity.ERROR)

    @property
    def warnings(self) -> int:
        return sum(g.count for g in self.groups if g.severity is Severity.WARNING)

    @property
    def infos(self) -> int:
        return sum(g.count for g in self.groups if g.severity is Severity.INFO)

    @property
    def exit_code(self) -> int:
        """CI contract: 0 clean, 1 warnings only, 2 errors found.

        Informational findings are reported but never fail a run: they are notes,
        not verdicts.
        """
        if self.errors:
            return 2
        if self.warnings:
            return 1
        return 0

    def to_dict(self) -> dict[str, Any]:
        """Machine-readable form. Stable enough for CI snapshots."""
        return {
            "payloads_scanned": self.payloads_scanned,
            "payloads_clean": self.payloads_clean,
            "payloads_failed": self.payloads_failed,
            "findings_total": self.findings_total,
            "errors": self.errors,
            "warnings": self.warnings,
            "infos": self.infos,
            "duration_ms": self.duration_ms,
            "groups": [
                {
                    "fingerprint": g.fingerprint,
                    "layer": g.layer.value,
                    "rule": g.rule,
                    "path": g.path,
                    "severity": g.severity.value,
                    "message": g.message,
                    "hint": g.hint,
                    "count": g.count,
                    "payloads_affected": g.payloads_affected,
                    "impact": round(g.impact, 3),
                    "sample_values": list(g.sample_values),
                    "correlated_with": list(g.correlated_with),
                }
                for g in self.groups
            ],
        }
