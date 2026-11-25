"""Layer 4: corruption that passes every schema."""

from __future__ import annotations

import pytest

from assay.models import Layer, Severity
from assay.strings import check


def rules(document: object) -> list[str]:
    return [finding.rule for finding in check(document, "p1")]


@pytest.mark.parametrize(
    ("value", "rule"),
    [
        ("Jos\u00c3\u00a9", "mojibake"),
        ("Smith\u200b", "zero-width"),
        ("Smith\ufeff", "zero-width"),
        ("New\u00a0York", "non-breaking-space"),
        ("bad\u0007name", "control-character"),
        (" Smith", "whitespace"),
        ("Smith ", "whitespace"),
        ('{"a": 1}', "double-encoded"),
        ("[1, 2]", "double-encoded"),
        ("Mu\ufffdoz", "replacement-character"),
        ("Vl\u0430dimir", "mixed-script"),
    ],
)
def test_each_corruption_is_found(value: str, rule: str) -> None:
    findings = check({"name": value}, "p1")
    assert rule in [finding.rule for finding in findings]
    finding = next(finding for finding in findings if finding.rule == rule)
    assert finding.path == "name"
    assert finding.layer is Layer.STRING
    assert finding.payload_id == "p1"


@pytest.mark.parametrize(
    "value",
    ["Ada", "José", "New York", "E-000001", "O'Brien", "line\nbreak", "{not json", "[]x"],
)
def test_clean_strings_are_clean(value: str) -> None:
    assert check({"name": value}, "p1") == []


def test_paths_point_at_the_string_field() -> None:
    document = {"members": [{"first_name": "Ada"}, {"first_name": "Smith\u200b"}]}
    findings = check(document, "p1")
    assert [finding.path for finding in findings] == ["members[1].first_name"]


def test_control_characters_are_errors() -> None:
    finding = check({"name": "bad\u0007name"}, "p1")[0]
    assert finding.severity is Severity.ERROR
    assert "U+0007" in finding.message


def test_double_encoded_json_is_an_error() -> None:
    finding = check({"payload": '{"a": 1}'}, "p1")[0]
    assert finding.severity is Severity.ERROR
    assert finding.rule == "double-encoded"


def test_hidden_characters_are_spelled_out() -> None:
    finding = check({"name": "Smith\u200b"}, "p1")[0]
    assert "U+200B" in finding.message
    assert "invisible" in (finding.hint or "")


def test_mojibake_hint_shows_the_repair() -> None:
    finding = check({"name": "Jos\u00c3\u00a9"}, "p1")[0]
    assert finding.rule == "mojibake"
    assert "José" in (finding.hint or "")


def test_mixed_script_is_per_word_not_per_string() -> None:
    document = {"name": "Ada Lovelace Vl\u0430dimir"}
    finding = check(document, "p1")[0]
    assert finding.value_preview == "Vl\u0430dimir"


def test_multiple_problems_in_one_string_are_all_reported() -> None:
    findings = check({"name": " Smith\u200b "}, "p1")
    assert {finding.rule for finding in findings} == {"zero-width", "whitespace"}


def test_nested_structures_are_walked() -> None:
    document = {"members": [{"tags": ["ok", "bad\u200b"]}]}
    findings = check(document, "p1")
    assert findings[0].path == "members[0].tags[1]"


def test_empty_string_is_not_flagged() -> None:
    assert check({"name": ""}, "p1") == []
