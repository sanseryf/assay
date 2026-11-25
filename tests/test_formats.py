"""HTML and SARIF output."""

from __future__ import annotations

import json
from pathlib import Path

from assay.formats import to_html, to_sarif
from assay.loader import Payload
from assay.pipeline import Pipeline

SCHEMA = {
    "type": "object",
    "properties": {"members": {"type": "array", "items": {"type": "object"}}},
    "additionalProperties": False,
}


def sample_report():
    payloads = [
        Payload("p1", b'{"<script>": 1}'),
        Payload("p2", b'{"members": []}'),
    ]
    return Pipeline(schema=SCHEMA).run(payloads)


def test_html_is_a_standalone_escaped_page() -> None:
    page = to_html(sample_report())
    assert page.startswith("<!doctype html>")
    assert "Assay report" in page
    assert "&lt;script&gt;" in page
    assert "<script>" not in page
    assert "no scripts" not in page


def test_html_says_clean_when_there_is_nothing_to_report() -> None:
    report = Pipeline(schema=SCHEMA).run([Payload("p1", b'{"members": []}')])
    assert "No problems found" in to_html(report)


def test_sarif_is_valid_21_with_one_rule_per_group() -> None:
    document = json.loads(to_sarif(sample_report()))
    assert document["version"] == "2.1.0"
    run = document["runs"][0]
    assert run["tool"]["driver"]["name"] == "Assay"
    assert run["results"]
    assert {rule["id"] for rule in run["tool"]["driver"]["rules"]} == {
        result["ruleId"] for result in run["results"]
    }
    result = run["results"][0]
    assert result["level"] == "error"
    assert result["locations"][0]["physicalLocation"]["artifactLocation"]["uri"] == "p1"
    assert result["locations"][0]["logicalLocations"][0]["fullyQualifiedName"] == "<script>"


def test_sarif_levels_follow_severity() -> None:
    report = Pipeline().run([Payload("p1", '{"name": "Smith\u200b"}'.encode())])
    document = json.loads(to_sarif(report))
    assert document["runs"][0]["results"][0]["level"] == "warning"


def test_cli_writes_html_and_sarif_files(tmp_path: Path, capsys) -> None:
    from assay.cli import run

    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(SCHEMA), encoding="utf-8")
    payload_path = tmp_path / "payload.json"
    payload_path.write_text('{"<script>": 1}', encoding="utf-8")

    html_path = tmp_path / "report.html"
    assert (
        run(
            [
                "check",
                str(payload_path),
                "--schema",
                str(schema_path),
                "--format",
                "html",
                "--output",
                str(html_path),
            ]
        )
        == 2
    )
    assert "&lt;script&gt;" in html_path.read_text(encoding="utf-8")

    sarif_path = tmp_path / "report.sarif"
    assert (
        run(
            [
                "check",
                str(payload_path),
                "--schema",
                str(schema_path),
                "--format",
                "sarif",
                "--output",
                str(sarif_path),
            ]
        )
        == 2
    )
    document = json.loads(sarif_path.read_text(encoding="utf-8"))
    assert document["version"] == "2.1.0"
