"""Layer 3: rules that catch valid-but-wrong data."""

from __future__ import annotations

from typing import Any

from assay.models import Layer, Severity
from assay.pipeline import Pipeline
from assay.rules import RulesConfig
from assay.setup import SetupLayer


def config(rules: list[dict[str, Any]], **extra: Any) -> RulesConfig:
    return RulesConfig.model_validate({"rules": rules, **extra})


def findings_for(rules: list[dict[str, Any]], document: dict[str, Any]) -> list:
    layer = SetupLayer(config(rules))
    findings = layer.check(document, "p1")
    return findings


def batch_findings(rules: list[dict[str, Any]], documents: list[dict[str, Any]]) -> list:
    layer = SetupLayer(config(rules))
    found = []
    for index, document in enumerate(documents):
        found.extend(layer.check(document, f"p{index}"))
    for index, document in enumerate(documents):
        layer.observe(document, f"p{index}")
    found.extend(layer.finalize())
    return found


def compare_rule(**overrides: Any) -> dict[str, Any]:
    rule: dict[str, Any] = {
        "id": "dates-ordered",
        "kind": "compare",
        "left": "$.members[*].start_date",
        "op": "<=",
        "right": "$.members[*].end_date",
        "severity": "error",
        "hint": "End date precedes start date.",
    }
    rule.update(overrides)
    return rule


def test_compare_flags_a_swapped_date_range() -> None:
    document = {"members": [{"start_date": "2024-06-01", "end_date": "2024-01-01"}]}
    findings = findings_for([compare_rule()], document)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.layer is Layer.SETUP
    assert finding.rule == "dates-ordered"
    assert finding.path == "members[0].end_date"
    assert finding.severity is Severity.ERROR
    assert finding.hint == "End date precedes start date."


def test_compare_accepts_an_ordered_range() -> None:
    document = {"members": [{"start_date": "2024-01-01", "end_date": "2024-06-01"}]}
    assert findings_for([compare_rule()], document) == []


def test_compare_pairs_records_by_position() -> None:
    document = {
        "members": [
            {"start_date": "2024-01-01", "end_date": "2024-06-01"},
            {"start_date": "2024-09-01", "end_date": "2024-03-01"},
        ]
    }
    findings = findings_for([compare_rule()], document)
    assert [finding.path for finding in findings] == ["members[1].end_date"]


def test_compare_broadcasts_a_single_value() -> None:
    document = {
        "batch_date": "2024-01-01",
        "members": [{"end_date": "2024-06-01"}, {"end_date": "2023-01-01"}],
    }
    rules = [
        compare_rule(
            left="$.batch_date",
            right="$.members[*].end_date",
        )
    ]
    findings = findings_for(rules, document)
    assert [finding.path for finding in findings] == ["members[1].end_date"]


def test_compare_skips_missing_values() -> None:
    document = {"members": [{"start_date": "2024-09-01"}]}
    assert findings_for([compare_rule()], document) == []


def test_compare_skips_incomparable_values() -> None:
    document = {"members": [{"start_date": "later", "end_date": 5}]}
    assert findings_for([compare_rule()], document) == []


def test_required_if_flags_a_missing_dependent_field() -> None:
    rules = [
        {
            "id": "dependent-relationship",
            "kind": "required_if",
            "path": "$.members[*].type",
            "equals": "dependent",
            "require": "relationship",
            "severity": "error",
        }
    ]
    document = {
        "members": [
            {"type": "employee"},
            {"type": "dependent"},
        ]
    }
    findings = findings_for(rules, document)
    assert len(findings) == 1
    assert findings[0].path == "members[1].relationship"
    assert "dependent" in findings[0].message


def test_required_if_accepts_the_field_when_present() -> None:
    rules = [
        {
            "id": "dependent-relationship",
            "kind": "required_if",
            "path": "$.members[*].type",
            "equals": "dependent",
            "require": "relationship",
        }
    ]
    document = {"members": [{"type": "dependent", "relationship": "spouse"}]}
    assert findings_for(rules, document) == []


def test_placeholder_values_are_flagged() -> None:
    rules = [{"id": "placeholder-name", "kind": "placeholder"}]
    document = {"members": [{"first_name": "TEST"}, {"first_name": "Ada"}]}
    findings = findings_for(rules, document)
    assert [finding.path for finding in findings] == ["members[0].first_name"]
    assert findings[0].severity is Severity.WARNING


def test_placeholder_rule_can_be_scoped_to_paths() -> None:
    rules = [{"id": "placeholder-name", "kind": "placeholder", "paths": ["$.members[*].last_name"]}]
    document = {"members": [{"first_name": "TEST", "last_name": "N/A"}]}
    findings = findings_for(rules, document)
    assert [finding.path for finding in findings] == ["members[0].last_name"]


def test_constant_smell_flags_the_shared_value() -> None:
    rules = [
        {
            "id": "hire-date-default",
            "kind": "constant_smell",
            "path": "$.members[*].hire_date",
            "threshold": 0.95,
            "min_records": 5,
        }
    ]
    documents = [{"members": [{"hire_date": "1900-01-01"}]} for _ in range(19)] + [
        {"members": [{"hire_date": "2020-01-01"}]}
    ]
    findings = batch_findings(rules, documents)
    assert len(findings) == 19
    assert "95%" in findings[0].message or "of records" in findings[0].message
    assert findings[0].rule == "hire-date-default"


def test_constant_smell_ignores_a_varied_field() -> None:
    rules = [
        {
            "id": "hire-date-default",
            "kind": "constant_smell",
            "path": "$.members[*].hire_date",
            "threshold": 0.95,
            "min_records": 5,
        }
    ]
    documents = [{"members": [{"hire_date": f"2020-01-{index + 1:02d}"}]} for index in range(10)]
    assert batch_findings(rules, documents) == []


def test_case_drift_flags_the_minority_forms() -> None:
    rules = [
        {
            "id": "tier-case",
            "kind": "case_drift",
            "path": "$.members[*].coverage_tier",
            "min_records": 2,
        }
    ]
    documents = [
        {"members": [{"coverage_tier": "EMPLOYEE"}]},
        {"members": [{"coverage_tier": "EMPLOYEE"}]},
        {"members": [{"coverage_tier": "employee"}]},
        {"members": [{"coverage_tier": "Employee"}]},
    ]
    findings = batch_findings(rules, documents)
    assert len(findings) == 2
    assert {finding.value_preview for finding in findings} == {"employee", "Employee"}
    assert "EMPLOYEE" in (findings[0].hint or "")


def test_duplicate_records_are_reported_once_per_extra_occurrence() -> None:
    rules = [
        {
            "id": "duplicate-member",
            "kind": "duplicate_records",
            "key": "$.members[*].external_id",
        }
    ]
    documents = [
        {"members": [{"external_id": "E-1"}]},
        {"members": [{"external_id": "E-2"}]},
        {"members": [{"external_id": "E-1"}]},
        {"members": [{"external_id": "E-1"}]},
    ]
    findings = batch_findings(rules, documents)
    assert len(findings) == 2
    assert findings[0].path == "members[0].external_id"
    assert "first seen in p0" in (findings[0].hint or "")


def test_type_inconsistency_flags_numbers_next_to_strings() -> None:
    rules = [
        {
            "id": "zip-type",
            "kind": "type_inconsistency",
            "paths": ["$.members[*].zip"],
            "min_records": 3,
        }
    ]
    documents = [
        {"members": [{"zip": "75071"}]},
        {"members": [{"zip": "07030"}]},
        {"members": [{"zip": 7030}]},
        {"members": [{"zip": 75071}]},
    ]
    findings = batch_findings(rules, documents)
    assert len(findings) == 2
    assert all("string elsewhere" in finding.message for finding in findings)
    assert {finding.payload_id for finding in findings} == {"p2", "p3"}


def test_type_inconsistency_ignores_uniform_fields() -> None:
    rules = [{"id": "zip-type", "kind": "type_inconsistency", "paths": ["$.members[*].zip"]}]
    documents = [{"members": [{"zip": "75071"}]} for _ in range(6)]
    assert batch_findings(rules, documents) == []


def test_pipeline_runs_setup_rules(tmp_path) -> None:
    from assay.loader import Payload

    rules = config([compare_rule()])
    payload = Payload(
        "p1",
        b'{"members": [{"start_date": "2024-09-01", "end_date": "2024-01-01"}]}',
    )
    report = Pipeline(rules=rules).run([payload])
    assert report.groups[0].layer is Layer.SETUP
    assert report.exit_code == 2


def test_warning_rules_give_exit_code_one() -> None:
    from assay.loader import Payload

    rules = config([{"id": "placeholder-name", "kind": "placeholder"}])
    payload = Payload("p1", b'{"first_name": "TEST"}')
    report = Pipeline(rules=rules).run([payload])
    assert report.exit_code == 1
