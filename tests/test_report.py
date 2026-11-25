"""Report rendering: the terminal table and the JSON contract."""

from __future__ import annotations

import json

from assay.grouping import group_findings
from assay.models import Finding, Layer, Report, Severity
from assay.pipeline import Pipeline
from assay.report import print_report, summary_line, to_json, to_text

SCHEMA = {
    "type": "object",
    "properties": {
        "members": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"zip": {"type": "string"}},
            },
        }
    },
}

BATCH = [
    '{"members": [{"zip": 7030}, {"zip": 2134}, {"zip": 501}]}',
    '{"members": [{"zip": 75071}]}',
    '{"members": [{"zip": "clean"}]}',
]


def batch_report() -> Report:
    from assay.loader import Payload

    payloads = [
        Payload(payload_id=f"payload_{index:03d}.json", raw=text.encode())
        for index, text in enumerate(BATCH, start=1)
    ]
    return Pipeline(schema=SCHEMA).run(payloads)


def test_summary_line_reports_counts() -> None:
    report = batch_report()
    rendered = summary_line(report).plain
    assert rendered.startswith("Assay")
    assert "4 payloads" not in rendered  # 3 payloads
    assert "3 payloads" in rendered
    assert "1 clean" in rendered
    assert "2 with errors" in rendered


def test_terminal_table_shows_the_group_and_its_hint() -> None:
    text = to_text(batch_report())
    assert "schema" in text
    assert "members[*].zip" in text
    assert "integer, expected string" in text
    assert "hint:" in text
    assert "7030" in text
    assert "clears every finding" in text


def test_clean_report_says_so() -> None:
    from assay.loader import Payload

    report = Pipeline(schema=SCHEMA).run([Payload("p", b'{"members": []}')])
    text = to_text(report)
    assert "no problems found" in text


def test_syntax_group_shows_file_line_and_column() -> None:
    from assay.loader import Payload

    report = Pipeline(schema=None).run([Payload("payload_0441.json", b'{"a": 1,}')])
    text = to_text(report)
    assert "payload_0441.json:1:8" in text
    assert "trailing comma" in text


def test_json_output_is_stable_and_serializable() -> None:
    report = batch_report()
    payload = json.loads(to_json(report))
    assert payload["payloads_scanned"] == 3
    assert payload["payloads_clean"] == 1
    assert payload["errors"] == 4
    assert payload["groups"][0]["path"] == "members[*].zip"
    assert payload["groups"][0]["payloads_affected"] == 2
    assert payload["groups"][0]["sample_values"] == ["7030", "2134", "501"]
    assert "duration_ms" in payload


def test_correlation_note_is_rendered() -> None:
    findings = []
    for index in range(6):
        findings.append(
            Finding(
                layer=Layer.SCHEMA,
                rule="format",
                severity=Severity.ERROR,
                path=f"members[{index}].dob",
                message="not a valid date",
                signature="fmt:date",
                payload_id=f"p{index}",
            )
        )
        findings.append(
            Finding(
                layer=Layer.SCHEMA,
                rule="format",
                severity=Severity.ERROR,
                path=f"members[{index}].hire_date",
                message="not a valid date",
                signature="fmt:date",
                payload_id=f"p{index}",
            )
        )
    from assay.loader import Payload  # noqa: F401  (keeps imports honest at top)

    report = Report(payloads_scanned=6, payloads_clean=0, groups=group_findings(findings))
    text = to_text(report)
    assert "correlated" in text
    assert "co-occur in 100%" in text


def test_print_report_accepts_a_custom_console() -> None:
    from rich.console import Console

    console = Console(record=True, width=120, no_color=True)
    print_report(batch_report(), console=console)
    output = console.export_text()
    assert "members[*].zip" in output


def test_ascii_fallback_replaces_decoration_only() -> None:
    report = batch_report()
    plain = to_text(report, ascii_only=True)
    assert "█" not in plain
    assert "→" not in plain
    assert "#" in plain  # the impact bar
    assert "members[*].zip" in plain  # payload paths are untouched
    assert "7030" in plain


def test_ascii_fallback_keeps_characters_that_are_the_problem() -> None:
    """A curly quote in a hint is data: cp1252 can print it, so it must survive."""
    from assay.loader import Payload

    report = Pipeline(schema=None).run([Payload("p1", "{\u201ca\u201d: 1}".encode())])
    plain = to_text(report, ascii_only=True)
    assert "\u201c" in plain
    assert 'replace "\u201d' not in plain  # it was not flattened into straight quotes
