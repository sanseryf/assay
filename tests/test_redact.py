"""Redaction: values out, paths and shapes in."""

from __future__ import annotations

from assay.loader import Payload
from assay.pipeline import Pipeline
from assay.redact import collect_values, scrub_text, shape
from assay.rules import RulesConfig


def test_shapes_for_names_dates_and_numbers() -> None:
    assert shape("John Smith") == "J*** S****"
    assert shape("75071") == "#####"
    assert shape("1990-03-14") == "####-##-##"
    assert shape(75071) == "#####"
    assert shape(True) == "true"


def test_collect_values_skips_tiny_scalars() -> None:
    values = collect_values({"name": "John Smith", "state": "TX", "zip": 75071})
    assert "John Smith" in values
    assert "75071" in values
    assert "TX" not in values


def test_scrub_replaces_known_values_in_free_text() -> None:
    values = collect_values({"zip": "75071"})
    assert scrub_text("75071 belongs to TX", values) == "##### belongs to TX"


def test_compare_hint_and_preview_are_redacted() -> None:
    rules = RulesConfig.model_validate(
        {
            "rules": [
                {
                    "id": "dates",
                    "kind": "compare",
                    "left": "$.start",
                    "op": "<=",
                    "right": "$.end",
                    "hint": "saw 2024-01-01 on the right",
                }
            ]
        }
    )
    report = Pipeline(rules=rules, redact=True).run(
        [Payload("p1", b'{"start": "2024-09-01", "end": "2024-01-01"}')]
    )
    group = report.groups[0]
    assert report.redacted
    assert group.sample_values == ["####-##-##"]
    assert "2024-01-01" not in (group.hint or "")
    assert "####-##-##" in (group.hint or "")


def test_mojibake_hint_is_dropped_not_scrubbed() -> None:
    report = Pipeline(redact=True).run([Payload("p1", '{"name": "Jos\u00c3\u00a9"}'.encode())])
    group = report.groups[0]
    assert group.rule == "mojibake"
    assert group.hint is None


def test_paths_and_counts_are_untouched() -> None:
    schema = {
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
    report = Pipeline(schema=schema, redact=True).run(
        [Payload("p1", b'{"members": [{"zip": 75071}]}')]
    )
    assert report.groups[0].path == "members[*].zip"


def test_report_json_carries_the_redacted_flag() -> None:
    import json

    from assay.report import to_json

    report = Pipeline(redact=True).run([Payload("p1", b'{"zip": 75071}')])
    assert json.loads(to_json(report))["redacted"] is True
