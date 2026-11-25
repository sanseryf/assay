"""The CLI contract: exit codes, formats, and failing loudly when Assay cannot run."""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from assay import __version__
from assay.cli import run

SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "required": ["batch_id", "members"],
    "properties": {
        "batch_id": {"type": "string"},
        "members": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["first_name", "zip"],
                "properties": {
                    "first_name": {"type": "string"},
                    "zip": {"type": "string"},
                },
            },
        },
    },
}

GOOD = '{"batch_id": "B-1", "members": [{"first_name": "Ada", "zip": "75071"}]}'
BAD = '{"batch_id": "B-2", "members": [{"first_name": "Grace", "zip": 7030}]}'


def setup_files(tmp_path: Path, payload: str, *, schema: object = SCHEMA) -> tuple[Path, Path]:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(schema), encoding="utf-8")
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(payload, encoding="utf-8")
    return schema_path, payload_path


def invoke(schema_path: Path, *args: str) -> int:
    return run(["check", *args, "--schema", str(schema_path)])


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["--version"]) == 0
    assert __version__ in capsys.readouterr().out


def test_help(capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["check", "--help"]) == 0
    output = capsys.readouterr().out
    assert "--schema" in output
    assert "--format" in output


def test_clean_payload_exits_zero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    schema_path, payload_path = setup_files(tmp_path, GOOD)
    assert invoke(schema_path, str(payload_path)) == 0
    assert "no problems found" in capsys.readouterr().out


def test_errors_exit_two(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    schema_path, payload_path = setup_files(tmp_path, BAD)
    assert invoke(schema_path, str(payload_path)) == 2
    assert "members[*].zip" in capsys.readouterr().out


def test_json_format_is_machine_readable(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    schema_path, payload_path = setup_files(tmp_path, BAD)
    assert invoke(schema_path, str(payload_path), "--format", "json") == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["payloads_scanned"] == 1
    assert payload["groups"][0]["rule"] == "type"


def test_output_file(tmp_path: Path) -> None:
    schema_path, payload_path = setup_files(tmp_path, BAD)
    report_path = tmp_path / "report.json"
    code = invoke(schema_path, str(payload_path), "--format", "json", "--output", str(report_path))
    assert code == 2
    payload = json.loads(report_path.read_text(encoding="utf-8"))
    assert payload["payloads_scanned"] == 1


def test_quiet_prints_nothing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    schema_path, payload_path = setup_files(tmp_path, BAD)
    assert invoke(schema_path, str(payload_path), "--quiet") == 2
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_ndjson_batch(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    schema_path = tmp_path / "schema.json"
    schema_path.write_text(json.dumps(SCHEMA), encoding="utf-8")
    batch = tmp_path / "batch.ndjson"
    batch.write_text(GOOD + "\n" + BAD + "\n", encoding="utf-8")
    assert invoke(schema_path, str(batch), "--format", "json") == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["payloads_scanned"] == 2


def test_stdin(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    schema_path, _ = setup_files(tmp_path, GOOD)
    monkeypatch.setattr(sys, "stdin", SimpleNamespace(buffer=io.BytesIO(GOOD.encode())))
    assert invoke(schema_path, "-", "--format", "json") == 0
    assert json.loads(capsys.readouterr().out)["payloads_scanned"] == 1


def test_missing_input_exits_three(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    schema_path, _ = setup_files(tmp_path, GOOD)
    assert invoke(schema_path, str(tmp_path / "does-not-exist.json")) == 3
    assert "no files matched" in capsys.readouterr().err


def test_invalid_schema_json_exits_three(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    schema_path = tmp_path / "broken-schema.json"
    schema_path.write_text("{not json", encoding="utf-8")
    payload_path = tmp_path / "payload.json"
    payload_path.write_text(GOOD, encoding="utf-8")
    assert invoke(schema_path, str(payload_path)) == 3
    assert "not valid JSON" in capsys.readouterr().err


def test_semantically_invalid_schema_exits_three(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    schema_path, payload_path = setup_files(tmp_path, GOOD, schema={"type": "not-a-type"})
    assert invoke(schema_path, str(payload_path)) == 3
    assert "invalid schema" in capsys.readouterr().err


def test_unknown_format_exits_three(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    schema_path, payload_path = setup_files(tmp_path, GOOD)
    assert invoke(schema_path, str(payload_path), "--format", "yaml") == 3
    assert "unknown --format" in capsys.readouterr().err


def test_empty_ndjson_exits_three(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    schema_path, _ = setup_files(tmp_path, GOOD)
    empty = tmp_path / "empty.ndjson"
    empty.write_text("\n\n", encoding="utf-8")
    assert invoke(schema_path, str(empty)) == 3
    assert "no payloads found" in capsys.readouterr().err


def test_usage_error_exits_three(capsys: pytest.CaptureFixture[str]) -> None:
    """Exit 2 means "errors found"; a typo on the command line must not look like one."""
    assert run(["check"]) == 3
    assert "Missing argument" in capsys.readouterr().err


def test_internal_error_exits_three(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A bug in Assay must be exit 3, and it must be visible, not silent."""
    from assay import cli

    schema_path, payload_path = setup_files(tmp_path, GOOD)

    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError("boom")

    # Under pytest stdout is a pipe, so the plain-text path is the one that runs.
    monkeypatch.setattr(cli, "to_text", explode)
    monkeypatch.setattr(
        sys, "argv", ["assay", "check", str(payload_path), "--schema", str(schema_path)]
    )
    with pytest.raises(SystemExit) as info:
        cli.main()
    assert info.value.code == 3
