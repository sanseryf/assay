"""Layer 3: valid JSON that matches the schema and is still wrong.

These rules catch mapping and configuration mistakes: a date range with the ends
swapped, a field that is a placeholder on every record, an ID that is a string in
some payloads and a number in others. The checks run in two phases because half of
them are properties of the batch, not of one payload:

* per payload: ``compare``, ``required_if``, ``placeholder``;
* across the batch: ``constant_smell``, ``case_drift``, ``duplicate_records``,
  ``type_inconsistency``, plus duplicate keys found while parsing.

Batch state is bounded: every collector stops recording after a fixed number of
observations, so a pathological batch degrades by losing a finding, never by
exhausting memory.
"""

from __future__ import annotations

import operator
from collections import Counter, defaultdict
from collections.abc import Iterable, Iterator
from typing import Any

from .fingerprint import normalize_path
from .hints import json_type, preview
from .jsonpath import leaf_paths, resolve, sibling
from .models import Finding, Layer, Severity
from .paths import walk
from .rules import (
    CaseDriftRule,
    CompareRule,
    ConstantSmellRule,
    DuplicateRecordsRule,
    PlaceholderRule,
    RequiredIfRule,
    RulesConfig,
    TypeInconsistencyRule,
)

MAX_OBSERVATIONS = 200_000

_OPS = {
    "<=": operator.le,
    "<": operator.lt,
    ">=": operator.ge,
    ">": operator.gt,
    "==": operator.eq,
    "!=": operator.ne,
}

Observation = tuple[str, str, Any]


def _is_scalar(value: object) -> bool:
    return not isinstance(value, dict | list)


def _display(expr: str) -> str:
    return expr[2:] if expr.startswith("$.") else expr


def _pairs(left: list[tuple[str, Any]], right: list[tuple[str, Any]]) -> Iterator[tuple[Any, Any]]:
    """Pair resolved values: broadcast a single value, otherwise pair by position."""
    if len(left) == 1 and len(right) > 1:
        for item in right:
            yield left[0], item
    elif len(right) == 1 and len(left) > 1:
        for item in left:
            yield item, right[0]
    else:
        yield from zip(left, right, strict=False)


class SetupLayer:
    """Evaluate the configured setup rules over a payload, then over the batch."""

    def __init__(self, config: RulesConfig | None = None) -> None:
        self.config = config or RulesConfig()
        self._compare: list[CompareRule] = [
            rule for rule in self.config.rules if isinstance(rule, CompareRule)
        ]
        self._required: list[RequiredIfRule] = [
            rule for rule in self.config.rules if isinstance(rule, RequiredIfRule)
        ]
        self._placeholder: list[PlaceholderRule] = [
            rule for rule in self.config.rules if isinstance(rule, PlaceholderRule)
        ]
        self._constant: list[ConstantSmellRule] = [
            rule for rule in self.config.rules if isinstance(rule, ConstantSmellRule)
        ]
        self._case: list[CaseDriftRule] = [
            rule for rule in self.config.rules if isinstance(rule, CaseDriftRule)
        ]
        self._duplicates: list[DuplicateRecordsRule] = [
            rule for rule in self.config.rules if isinstance(rule, DuplicateRecordsRule)
        ]
        self._types: list[TypeInconsistencyRule] = [
            rule for rule in self.config.rules if isinstance(rule, TypeInconsistencyRule)
        ]

        self._constant_values: dict[str, list[Observation]] = {
            rule.id: [] for rule in self._constant
        }
        self._case_values: dict[str, list[Observation]] = {rule.id: [] for rule in self._case}
        self._seen_keys: dict[str, dict[tuple[str, Any], tuple[str, str]]] = {
            rule.id: {} for rule in self._duplicates
        }
        self._type_values: dict[str, dict[str, dict[str, list[tuple[str, str, str]]]]] = {
            rule.id: defaultdict(lambda: defaultdict(list)) for rule in self._types
        }
        self._duplicate_findings: list[Finding] = []
        self._observations = 0
        self._truncated = False

    # ------------------------------------------------------------------ per payload

    def check(self, document: Any, payload_id: str) -> list[Finding]:
        findings: list[Finding] = []
        for compare_rule in self._compare:
            findings.extend(self._check_compare(compare_rule, document, payload_id))
        for required_rule in self._required:
            findings.extend(self._check_required(required_rule, document, payload_id))
        for placeholder_rule in self._placeholder:
            findings.extend(self._check_placeholder(placeholder_rule, document, payload_id))
        return findings

    def _check_compare(self, rule: CompareRule, document: Any, payload_id: str) -> list[Finding]:
        left = resolve(document, rule.left)
        right = resolve(document, rule.right)
        findings: list[Finding] = []
        for (_, left_value), (right_path, right_value) in _pairs(left, right):
            if left_value is None or right_value is None:
                continue
            try:
                passed = _OPS[rule.op](left_value, right_value)
            except TypeError:
                continue  # the values are not comparable; a type rule will say why
            if passed:
                continue
            message = rule.message or (
                f"{_display(rule.left)} must be {rule.op} {_display(rule.right)}"
            )
            findings.append(
                self._finding(
                    rule,
                    right_path,
                    message,
                    payload_id,
                    hint=rule.hint
                    or "check the field mapping or timezone; the two fields disagree.",
                    preview_value=preview(right_value),
                )
            )
        return findings

    def _check_required(
        self, rule: RequiredIfRule, document: Any, payload_id: str
    ) -> list[Finding]:
        required_expr = _sibling_expr(rule.path, rule.require)
        present = {path for path, _ in resolve(document, required_expr)}
        findings: list[Finding] = []
        for path, value in resolve(document, rule.path):
            if value != rule.equals:
                continue
            required_path = sibling(path, rule.require)
            if required_path in present:
                continue
            field = _last_field(rule.path)
            message = rule.message or (
                f"missing `{rule.require}` when `{field}` is {rule.equals!r}"
            )
            findings.append(
                self._finding(
                    rule,
                    required_path,
                    message,
                    payload_id,
                    hint=rule.hint
                    or "add the field, or remove the condition in the source mapping.",
                )
            )
        return findings

    def _check_placeholder(
        self, rule: PlaceholderRule, document: Any, payload_id: str
    ) -> list[Finding]:
        placeholders = {value.casefold() for value in rule.values}
        findings: list[Finding] = []
        for path, value in _strings(document, rule.paths):
            if value.strip().casefold() not in placeholders:
                continue
            findings.append(
                self._finding(
                    rule,
                    path,
                    "placeholder value",
                    payload_id,
                    hint=rule.hint
                    or "looks like a default or placeholder; check the source mapping.",
                    preview_value=value,
                )
            )
        return findings

    # ------------------------------------------------------------------ batch input

    def observe(self, document: Any, payload_id: str) -> None:
        if self._truncated:
            return
        for constant_rule in self._constant:
            for path, value in resolve(document, constant_rule.path):
                if _is_scalar(value):
                    self._record(self._constant_values[constant_rule.id], (payload_id, path, value))
        for case_rule in self._case:
            for path, value in resolve(document, case_rule.path):
                if isinstance(value, str):
                    self._record(self._case_values[case_rule.id], (payload_id, path, value))
        for duplicate_rule in self._duplicates:
            for path, value in resolve(document, duplicate_rule.key):
                if not _is_scalar(value):
                    continue
                lookup = self._seen_keys[duplicate_rule.id]
                key = (json_type(value), value)
                first = lookup.get(key)
                if first is None:
                    lookup[key] = (payload_id, path)
                else:
                    self._duplicate_findings.append(
                        self._finding(
                            duplicate_rule,
                            path,
                            "duplicate key in this batch",
                            payload_id,
                            hint=duplicate_rule.hint
                            or f"first seen in {first[0]}; dedupe or make the write an upsert.",
                            preview_value=preview(value),
                        )
                    )
        for type_rule in self._types:
            for path, value in self._type_candidates(document, type_rule):
                actual = json_type(value)
                by_type = self._type_values[type_rule.id][normalize_path(path)]
                bucket = by_type[actual]
                if self._observations < MAX_OBSERVATIONS:
                    bucket.append((payload_id, path, preview(value)))
                    self._observations += 1
                else:
                    self._truncated = True

    def _type_candidates(
        self, document: Any, rule: TypeInconsistencyRule
    ) -> Iterator[tuple[str, Any]]:
        if rule.paths is None:
            yield from ((path, value) for path, value in leaf_paths(document) if _is_scalar(value))
            return
        for expr in rule.paths:
            for path, value in resolve(document, expr):
                if _is_scalar(value):
                    yield path, value

    def _record(self, bucket: list[Observation], item: Observation) -> None:
        if self._observations >= MAX_OBSERVATIONS:
            self._truncated = True
            return
        bucket.append(item)
        self._observations += 1

    # ------------------------------------------------------------------ batch output

    def finalize(self) -> list[Finding]:
        findings: list[Finding] = []
        findings.extend(self._duplicate_findings)
        for constant_rule in self._constant:
            findings.extend(self._finalize_constant(constant_rule))
        for case_rule in self._case:
            findings.extend(self._finalize_case(case_rule))
        for type_rule in self._types:
            findings.extend(self._finalize_types(type_rule))
        return findings

    def _finalize_constant(self, rule: ConstantSmellRule) -> list[Finding]:
        observations = self._constant_values[rule.id]
        if len(observations) < rule.min_records:
            return []
        counts = Counter(_scalar_key(value) for _, _, value in observations)
        common, count = counts.most_common(1)[0]
        share = count / len(observations)
        if share < rule.threshold:
            return []
        findings: list[Finding] = []
        for payload_id, path, value in observations:
            if _scalar_key(value) != common:
                continue
            findings.append(
                self._finding(
                    rule,
                    path,
                    f"{share:.0%} of records share one value",
                    payload_id,
                    hint=rule.hint
                    or "a constant across records usually means the field is unmapped "
                    "and falls back to a default.",
                    preview_value=preview(value),
                )
            )
        return findings

    def _finalize_case(self, rule: CaseDriftRule) -> list[Finding]:
        observations = self._case_values[rule.id]
        if len(observations) < rule.min_records:
            return []
        by_folded: dict[str, list[Observation]] = defaultdict(list)
        for payload_id, path, value in observations:
            by_folded[value.casefold()].append((payload_id, path, value))
        findings: list[Finding] = []
        for items in by_folded.values():
            forms = Counter(value for _, _, value in items)
            if len(forms) < 2:
                continue
            majority, _ = forms.most_common(1)[0]
            for payload_id, path, value in items:
                if value == majority:
                    continue
                findings.append(
                    self._finding(
                        rule,
                        path,
                        "case differs from the rest of the batch",
                        payload_id,
                        hint=rule.hint
                        or f"the batch predominantly uses {majority!r}; make the field consistent.",
                        preview_value=value,
                    )
                )
        return findings

    def _finalize_types(self, rule: TypeInconsistencyRule) -> list[Finding]:
        findings: list[Finding] = []
        for _, by_type in self._type_values[rule.id].items():
            if "string" not in by_type:
                continue
            numeric = [kind for kind in by_type if kind in {"integer", "number"}]
            if not numeric:
                continue
            for kind in numeric:
                for payload_id, path, preview_value in by_type[kind]:
                    findings.append(
                        self._finding(
                            rule,
                            path,
                            f"{kind} here, string elsewhere in the batch",
                            payload_id,
                            hint=rule.hint
                            or "leading zeros or unquoted IDs are being lost; normalize the "
                            "type in the source mapping.",
                            preview_value=preview_value,
                        )
                    )
        return findings

    # ------------------------------------------------------------------ duplicate keys

    def duplicate_key_findings(
        self, payload_id: str, duplicates: Iterable[tuple[str, str]]
    ) -> list[Finding]:
        if not self.config.duplicate_keys:
            return []
        return [
            Finding(
                layer=Layer.SETUP,
                rule="duplicate-keys",
                severity=Severity.ERROR,
                path=path,
                message="duplicate key",
                hint="the later value silently wins; remove or rename the repeated key.",
                payload_id=payload_id,
            )
            for path, _ in duplicates
        ]

    # ------------------------------------------------------------------ helpers

    def _finding(
        self,
        rule: CompareRule
        | RequiredIfRule
        | ConstantSmellRule
        | PlaceholderRule
        | CaseDriftRule
        | DuplicateRecordsRule
        | TypeInconsistencyRule,
        path: str,
        message: str,
        payload_id: str,
        *,
        hint: str | None,
        preview_value: str | None = None,
    ) -> Finding:
        return Finding(
            layer=Layer.SETUP,
            rule=rule.id,
            severity=rule.severity,
            path=path,
            message=message,
            hint=hint,
            value_preview=preview_value,
            payload_id=payload_id,
        )


def _strings(document: Any, paths: list[str] | None) -> Iterator[tuple[str, str]]:
    if paths is None:
        for path, value in walk(document):
            if isinstance(value, str):
                yield path, value
        return
    for expr in paths:
        for path, value in resolve(document, expr):
            if isinstance(value, str):
                yield path, value


def _sibling_expr(expr: str, field: str) -> str:
    head, _, _ = expr.rpartition(".")
    if not head:
        return f"$.{field}"
    return f"{head}.{field}"


def _last_field(expr: str) -> str:
    return expr.rpartition(".")[2]


def _scalar_key(value: Any) -> tuple[str, Any]:
    """Counter key for a scalar: the type keeps ``1`` and ``True`` apart."""
    return json_type(value), value
