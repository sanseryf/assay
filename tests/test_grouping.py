"""Grouping: the collapse, the ranking, and the correlation note."""

from __future__ import annotations

from assay.fingerprint import fingerprint, normalize_path
from assay.grouping import group_findings, overlap_ratio
from assay.models import Finding, Group, Layer, Severity


def finding(
    path: str = "members[0].zip",
    *,
    payload_id: str = "p1",
    signature: str = "type:integer→string",
    layer: Layer = Layer.SCHEMA,
    rule: str = "type",
    message: str = "integer, expected string",
    preview: str | None = None,
    severity: Severity = Severity.ERROR,
) -> Finding:
    return Finding(
        layer=layer,
        rule=rule,
        severity=severity,
        path=path,
        message=message,
        signature=signature,
        value_preview=preview,
        payload_id=payload_id,
    )


def test_index_is_normalized() -> None:
    assert normalize_path("members[847].zip") == "members[*].zip"
    assert normalize_path("a[0].b[1][2].c") == "a[*].b[*][*].c"
    assert normalize_path("$") == "$"


def test_fingerprint_ignores_values_and_record_indexes() -> None:
    first = finding("members[0].zip", preview="7030")
    second = finding("members[847].zip", preview="2134")
    assert (
        fingerprint(first)
        == fingerprint(second)
        == "schema:type:members[*].zip:type:integer→string"
    )


def test_one_defect_across_records_collapses_to_one_group() -> None:
    findings = [finding(f"members[{index}].zip", payload_id=f"p{index}") for index in range(1912)]
    groups = group_findings(findings)
    assert len(groups) == 1
    group = groups[0]
    assert group.path == "members[*].zip"
    assert group.count == 1912
    assert group.payloads_affected == 1912
    assert group.impact == 1912.0


def test_two_independent_defects_stay_separate() -> None:
    findings = [
        finding("members[0].zip"),
        finding("members[1].zip"),
        finding("members[0].dob", signature="fmt:date", rule="format", message="not a valid date"),
    ]
    groups = group_findings(findings)
    assert len(groups) == 2


def test_groups_are_ranked_by_impact_then_layer() -> None:
    findings = [finding(f"members[{index}].zip", payload_id=f"a{index}") for index in range(5)]
    findings += [
        finding(
            "members[0].last_name",
            payload_id="b0",
            layer=Layer.STRING,
            rule="zero-width",
            signature="",
            message="zero-width space",
            severity=Severity.WARNING,
        )
    ]
    groups = group_findings(findings)
    assert groups[0].layer is Layer.SCHEMA
    assert groups[0].impact == 5.0
    assert groups[1].impact == 0.4


def test_earlier_layer_wins_an_impact_tie() -> None:
    wide = [finding(f"members[{index}].zip", payload_id=f"x{index}") for index in range(3)]
    syntax = [
        finding("$", payload_id=f"y{index}", layer=Layer.SYNTAX, rule="truncation", signature="")
        for index in range(3)
    ]
    groups = group_findings(wide + syntax)
    assert groups[0].layer is Layer.SYNTAX


def test_samples_are_distinct_and_capped() -> None:
    findings = [
        finding(f"members[{index}].zip", payload_id=f"p{index}", preview=value)
        for index, value in enumerate(["7030", "7030", "2134", "501", "999", "1234"])
    ]
    group = group_findings(findings)[0]
    assert group.sample_values == ["7030", "2134", "501"]


def test_correlation_flags_groups_hitting_the_same_records() -> None:
    findings = []
    for index in range(6):
        findings.append(
            finding(
                f"members[{index}].dob",
                payload_id=f"p{index}",
                rule="format",
                signature="fmt:date",
                message="not a valid date",
            )
        )
        findings.append(
            finding(
                f"members[{index}].hire_date",
                payload_id=f"p{index}",
                rule="format",
                signature="fmt:date",
                message="not a valid date",
            )
        )
    groups = group_findings(findings)
    assert len(groups) == 2
    assert groups[0].correlated_with == [groups[1].fingerprint]
    assert groups[1].correlated_with == [groups[0].fingerprint]
    assert overlap_ratio(groups[0], groups[1]) == 1.0


def test_correlation_ignores_disjoint_groups() -> None:
    findings = []
    for index in range(6):
        findings.append(
            finding(
                f"members[{index}].dob",
                payload_id=f"d{index}",
                rule="format",
                signature="fmt:date",
                message="not a valid date",
            )
        )
        findings.append(finding(f"members[{index}].zip", payload_id=f"z{index}"))
    groups = group_findings(findings)
    assert all(group.correlated_with == [] for group in groups)


def test_correlation_requires_enough_records() -> None:
    findings = []
    for index in range(3):
        findings.append(
            finding(
                f"members[{index}].dob",
                payload_id=f"p{index}",
                rule="format",
                signature="fmt:date",
                message="not a valid date",
            )
        )
        findings.append(
            finding(
                f"members[{index}].hire_date",
                payload_id=f"p{index}",
                rule="format",
                signature="fmt:date",
                message="not a valid date",
            )
        )
    groups = group_findings(findings)
    assert all(group.correlated_with == [] for group in groups)


def test_group_metadata_comes_from_the_first_finding() -> None:
    group = group_findings([finding("members[0].zip", preview="7030")])[0]
    assert group.message == "integer, expected string"
    assert group.severity is Severity.ERROR
    assert isinstance(group, Group)
