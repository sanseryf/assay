"""The pipeline and the input loader."""

from __future__ import annotations

import io
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from assay.grouping import group_findings
from assay.loader import InputError, Payload, expand_paths, read_payloads
from assay.models import Finding, Layer, Report, Severity
from assay.pipeline import Limits, Pipeline, max_depth
from assay.syntax import analyze

SCHEMA = {
    "type": "object",
    "required": ["id"],
    "properties": {"id": {"type": "integer"}},
    "additionalProperties": False,
}


def payload(text: str, payload_id: str) -> Payload:
    return Payload(payload_id=payload_id, raw=text.encode())


def test_clean_batch_is_clean() -> None:
    report = Pipeline(schema=SCHEMA).run([payload('{"id": 1}', "a"), payload('{"id": 2}', "b")])
    assert report.payloads_scanned == 2
    assert report.payloads_clean == 2
    assert report.groups == []
    assert report.exit_code == 0


def test_schema_errors_collapse_across_a_batch() -> None:
    report = Pipeline(schema=SCHEMA).run(
        [payload('{"id": "x"}', f"p{index}") for index in range(3)]
    )
    assert report.payloads_clean == 0
    assert len(report.groups) == 1
    assert report.groups[0].payloads_affected == 3
    assert report.exit_code == 2


def test_a_syntax_failure_does_not_stop_the_batch() -> None:
    report = Pipeline(schema=SCHEMA).run(
        [
            payload('{"id": 1,}', "broken"),
            payload('{"id": 2}', "good"),
            payload('{"id": "three"}', "schema_error"),
        ]
    )
    assert report.payloads_scanned == 3
    assert report.payloads_clean == 1
    rules = {group.rule for group in report.groups}
    assert rules == {"trailing-comma", "type"}


def test_schema_is_optional() -> None:
    report = Pipeline(schema=None).run([payload('{"id": 1}', "a")])
    assert report.payloads_clean == 1


def test_oversized_payload_is_reported_without_parsing() -> None:
    report = Pipeline(schema=SCHEMA, limits=Limits(max_bytes=5)).run(
        [Payload(payload_id="big", raw=b'{"id": 1}', oversized=True)]
    )
    assert report.groups[0].rule == "input-too-large"
    assert report.exit_code == 2


def test_too_deep_payload_is_reported() -> None:
    document = "[" * 20 + "]" * 20
    report = Pipeline(schema=SCHEMA, limits=Limits(max_depth=10)).run([payload(document, "deep")])
    assert report.groups[0].rule == "input-too-deep"


def test_extreme_nesting_is_reported_not_raised() -> None:
    """Whether the parser or the depth guard catches it depends on the platform.

    CPython's C scanner handles 3,000 levels on Linux and raises RecursionError on
    Windows; both paths must end in an ``input-too-deep`` finding, never a crash.
    """
    document = "[" * 3000 + "]" * 3000
    report = Pipeline(schema=None).run([payload(document, "deep")])
    assert report.groups[0].rule == "input-too-deep"


def test_max_depth_counts_containers() -> None:
    assert max_depth("scalar") == 1
    assert max_depth({"a": [{"b": 1}]}) == 4


def test_warnings_without_errors_exit_one() -> None:
    warning = Finding(
        layer=Layer.STRING,
        rule="zero-width",
        severity=Severity.WARNING,
        path="members[0].name",
        message="zero-width space",
        payload_id="p1",
    )
    report = Report(payloads_scanned=1, payloads_clean=0, groups=group_findings([warning]))
    assert report.exit_code == 1


def test_info_only_exits_zero() -> None:
    info = Finding(
        layer=Layer.STRING,
        rule="city-alias",
        severity=Severity.INFO,
        path="members[0].city",
        message="acceptable city variant",
        payload_id="p1",
    )
    report = Report(payloads_scanned=1, payloads_clean=0, groups=group_findings([info]))
    assert report.exit_code == 0


def test_ndjson_is_split_per_line_and_blank_lines_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    batch = tmp_path / "batch.ndjson"
    batch.write_text('{"id": 1}\n\n{"id": 2}\n', encoding="utf-8")
    payloads = list(read_payloads(["batch.ndjson"]))
    assert [item.payload_id for item in payloads] == ["batch.ndjson:1", "batch.ndjson:3"]


def test_payload_ids_stay_relative_to_the_working_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An absolute path in the WHERE column pushes the rest of the report off-screen."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "batch.ndjson").write_text('{"id": 1}\n', encoding="utf-8")
    payloads = list(read_payloads(["batch.ndjson"]))
    assert payloads[0].payload_id == "batch.ndjson:1"


def test_directory_input_keeps_json_and_ndjson_only(tmp_path: Path) -> None:
    (tmp_path / "a.json").write_text("{}", encoding="utf-8")
    (tmp_path / "b.ndjson").write_text("{}\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("ignore me", encoding="utf-8")
    found = expand_paths([str(tmp_path)])
    assert [Path(path).name for path in found] == ["a.json", "b.ndjson"]


def test_duplicates_are_checked_once(tmp_path: Path) -> None:
    one = tmp_path / "one.json"
    one.write_text("{}", encoding="utf-8")
    found = expand_paths([str(one), str(tmp_path / "*.json")])
    assert found == [str(one)]


def test_glob_without_matches_is_an_input_error() -> None:
    with pytest.raises(InputError):
        expand_paths(["does-not-exist-*.json"])


def test_missing_file_is_an_input_error(tmp_path: Path) -> None:
    with pytest.raises(InputError):
        list(read_payloads([str(tmp_path / "nope.json")]))


def test_oversized_file_is_sliced_not_read_whole(tmp_path: Path) -> None:
    big = tmp_path / "big.json"
    big.write_bytes(b"x" * 100)
    payloads = list(read_payloads([str(big)], max_bytes=10))
    assert payloads[0].oversized
    assert len(payloads[0].raw) == 10


def test_stdin_is_one_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = SimpleNamespace(buffer=io.BytesIO(b'{"id": 1}'))
    monkeypatch.setattr(sys, "stdin", fake)
    payloads = list(read_payloads(["-"]))
    assert [item.payload_id for item in payloads] == ["stdin"]
    assert payloads[0].raw == b'{"id": 1}'


def test_duplicate_keys_are_detected_during_parsing() -> None:
    result = analyze(b'{"a": 1, "a": 2, "nested": {"b": 3, "b": 4}}')
    assert result.ok
    assert result.duplicate_keys == [("a", "a"), ("nested.b", "b")]


def test_pipeline_reports_duplicate_keys() -> None:
    report = Pipeline(schema=None).run([Payload(payload_id="p1", raw=b'{"a": 1, "a": 2}')])
    assert report.groups[0].rule == "duplicate-keys"
    assert report.groups[0].layer is Layer.SETUP
    assert report.exit_code == 2


def test_string_damage_warns_without_failing_the_payload() -> None:
    report = Pipeline(schema=None).run(
        [Payload(payload_id="p1", raw='{"name": "Smith\u200b"}'.encode())]
    )
    group = report.groups[0]
    assert group.layer is Layer.STRING
    assert group.severity is Severity.WARNING
    assert report.exit_code == 1


def test_string_layer_can_be_disabled() -> None:
    from assay.rules import RulesConfig

    rules = RulesConfig.model_validate({"strings": {"enabled": False}})
    report = Pipeline(schema=None, rules=rules).run(
        [Payload(payload_id="p1", raw='{"name": "Smith\u200b"}'.encode())]
    )
    assert report.groups == []


def test_configured_length_limit_is_enforced() -> None:
    from assay.rules import RulesConfig

    rules = RulesConfig.model_validate({"strings": {"max_length": {"$.name": 4}}})
    report = Pipeline(schema=None, rules=rules).run(
        [Payload(payload_id="p1", raw=b'{"name": "Lovelace"}')]
    )
    assert report.groups[0].rule == "too-long"
    assert report.exit_code == 1
