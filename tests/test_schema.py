"""Layer 2: every violation, each with a usable hint."""

from __future__ import annotations

import pytest
from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from assay.schema import SchemaLayer

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["members"],
    "properties": {
        "members": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["first_name", "last_name", "zip", "dob"],
                "properties": {
                    "first_name": {"type": "string", "minLength": 1},
                    "last_name": {"type": "string"},
                    "zip": {"type": "string", "pattern": "^[0-9]{5}$"},
                    "dob": {"type": "string", "format": "date"},
                    "coverage_tier": {"enum": ["EMPLOYEE", "EMPLOYEE_SPOUSE", "EMPLOYEE_CHILD"]},
                    "age": {"type": "integer", "minimum": 0},
                },
                "additionalProperties": False,
            },
        }
    },
}


def layer() -> SchemaLayer:
    return SchemaLayer(SCHEMA)


def member(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "first_name": "Ada",
        "last_name": "Lovelace",
        "zip": "75071",
        "dob": "1990-03-14",
    }
    base.update(overrides)
    return base


def test_clean_document_has_no_findings() -> None:
    findings = layer().check({"members": [member()]}, "p1")
    assert findings == []


def test_required_reports_every_missing_field_with_its_own_path() -> None:
    findings = layer().check({"members": [{}]}, "p1")
    paths = {finding.path for finding in findings}
    assert paths == {
        "members[0].first_name",
        "members[0].last_name",
        "members[0].zip",
        "members[0].dob",
    }
    assert len(findings) == 4
    assert all(finding.message == "missing required field" for finding in findings)


def test_type_mismatch_groups_by_shape_and_hints_leading_zeros() -> None:
    findings = layer().check({"members": [member(zip=75071)]}, "p1")
    assert len(findings) == 1
    finding = findings[0]
    assert finding.path == "members[0].zip"
    assert finding.message == "integer, expected string"
    assert finding.signature == "type:integer→string"
    assert "leading zeros" in (finding.hint or "")
    assert finding.value_preview == "75071"


def test_type_mismatch_without_a_zip_like_name_gets_the_general_hint() -> None:
    findings = layer().check({"members": [member(age="thirty")]}, "p1")
    finding = findings[0]
    assert finding.message == "string, expected integer"
    assert "leading zeros" not in (finding.hint or "")


def test_enum_lists_allowed_values_and_suggests_a_closest_match() -> None:
    findings = layer().check({"members": [member(coverage_tier="employee")]}, "p1")
    finding = findings[0]
    assert finding.path == "members[0].coverage_tier"
    assert finding.message == "value not in allowed set"
    hint = finding.hint or ""
    assert "EMPLOYEE_SPOUSE" in hint
    assert "Did you mean 'EMPLOYEE'?" in hint


def test_unknown_field_suggests_the_real_field_name() -> None:
    findings = layer().check({"members": [member(frist_name="Ada")]}, "p1")
    finding = findings[0]
    assert finding.path == "members[0].frist_name"
    assert finding.message == "unknown field"
    assert "Did you mean 'first_name'?" in (finding.hint or "")


def test_format_is_asserted_not_just_annotated() -> None:
    document = {"members": [member(dob="03/14/1990")]}

    # The documented trap: without a format checker, jsonschema treats `format` as
    # an annotation and this document passes.
    assert list(Draft202012Validator(SCHEMA).iter_errors(document)) == []

    findings = layer().check(document, "p1")
    assert len(findings) == 1
    assert findings[0].rule == "format"
    assert findings[0].signature == "fmt:date"
    assert "YYYY-MM-DD" in (findings[0].hint or "")


def test_pattern_violation_names_the_expected_pattern() -> None:
    findings = layer().check({"members": [member(zip="7507")]}, "p1")
    finding = findings[0]
    assert finding.message == "does not match the expected pattern"
    assert "^[0-9]{5}$" in (finding.hint or "")


def test_every_violation_is_reported_at_once() -> None:
    findings = layer().check(
        {"members": [member(zip=75071, dob="03/14/1990", coverage_tier="nope")]},
        "p1",
    )
    assert len(findings) == 3
    assert {finding.rule for finding in findings} == {"type", "format", "enum"}


def test_findings_are_ordered_by_path() -> None:
    findings = layer().check({"members": [member(zip=75071, age=-1)]}, "p1")
    assert [finding.path for finding in findings] == [
        "members[0].age",
        "members[0].zip",
    ]


def test_root_level_type_error_has_the_root_path() -> None:
    findings = layer().check([], "p1")
    assert findings[0].path == "$"
    assert findings[0].message == "array, expected object"


def test_one_of_hint_reports_the_closest_variant() -> None:
    schema = {
        "type": "object",
        "properties": {
            "contact": {
                "oneOf": [
                    {
                        "type": "object",
                        "required": ["email"],
                        "properties": {"email": {"type": "string"}},
                    },
                    {
                        "type": "object",
                        "required": ["phone"],
                        "properties": {"phone": {"type": "string"}},
                    },
                ]
            }
        },
    }
    findings = SchemaLayer(schema).check({"contact": {"email": 5}}, "p1")
    assert findings
    assert all("closest variant" in (finding.hint or "") for finding in findings)


def test_invalid_schema_is_rejected_at_construction() -> None:
    with pytest.raises(SchemaError):
        SchemaLayer({"type": "not-a-real-type"})


def test_min_length_reports_the_limit() -> None:
    findings = layer().check({"members": [member(first_name="")]}, "p1")
    assert findings[0].message == "shorter than 1 characters"
    assert findings[0].signature == "minLength:1"
