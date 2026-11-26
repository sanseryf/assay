"""The `assay ticket` command."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assay.cli import run
from assay.tickets import Ticket

SCHEMA = {
    "type": "object",
    "required": ["members"],
    "properties": {
        "members": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["zip"],
                "properties": {"zip": {"type": "string", "pattern": "^[0-9]{5}$"}},
            },
        }
    },
}


@pytest.fixture()
def inputs(tmp_path: Path) -> tuple[Path, Path]:
    payload = tmp_path / "payload.json"
    payload.write_text(json.dumps({"members": [{"zip": 7030}]}), encoding="utf-8")
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
    return payload, schema


def test_ticket_prints_markdown(
    inputs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    payload, schema = inputs
    code = run(["ticket", str(payload), "--schema", str(schema)])
    assert code == 0
    output = capsys.readouterr().out
    assert "members[0].zip" in output
    assert "assay-schema-" in output
    assert "####" in output
    assert "7030" not in output


def test_ticket_prints_adf(inputs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]) -> None:
    payload, schema = inputs
    code = run(["ticket", str(payload), "--schema", str(schema), "--format", "adf"])
    assert code == 0
    document = json.loads(capsys.readouterr().out)
    assert document["type"] == "doc"


def test_ticket_writes_a_file(
    inputs: tuple[Path, Path], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    payload, schema = inputs
    output = tmp_path / "ticket.md"
    code = run(
        [
            "ticket",
            str(payload),
            "--schema",
            str(schema),
            "--output",
            str(output),
        ]
    )
    assert code == 0
    assert capsys.readouterr().out == ""
    assert "assay-schema-" in output.read_text(encoding="utf-8")


def test_clean_payload_has_no_ticket(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    payload = tmp_path / "clean.json"
    payload.write_text(json.dumps({"members": [{"zip": "75071"}]}), encoding="utf-8")
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps(SCHEMA), encoding="utf-8")
    code = run(["ticket", str(payload), "--schema", str(schema)])
    assert code == 3
    assert "no findings" in capsys.readouterr().err


def test_group_out_of_range_is_exit_3(
    inputs: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    payload, schema = inputs
    code = run(["ticket", str(payload), "--schema", str(schema), "--group", "9"])
    assert code == 3
    assert "out of range" in capsys.readouterr().err


def test_push_without_credentials_is_exit_3(
    inputs: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, schema = inputs
    for name in ("JIRA_URL", "JIRA_EMAIL", "JIRA_API_TOKEN", "JIRA_PROJECT"):
        monkeypatch.delenv(name, raising=False)
    code = run(["ticket", str(payload), "--schema", str(schema), "--push"])
    assert code == 3
    assert "--push needs" in capsys.readouterr().err


def test_push_creates_and_reports_the_key(
    inputs: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, schema = inputs
    captured: dict[str, object] = {}

    class FakeClient:
        def __init__(self, config: object) -> None:
            captured["config"] = config

        def __enter__(self) -> FakeClient:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def upsert(self, ticket: object) -> tuple[str, str]:
            captured["ticket"] = ticket
            return "created", "ABC-1"

    monkeypatch.setattr("assay.jira.JiraClient", FakeClient)
    monkeypatch.setenv("JIRA_URL", "https://example.atlassian.net")
    monkeypatch.setenv("JIRA_EMAIL", "me@example.com")
    monkeypatch.setenv("JIRA_API_TOKEN", "secret")
    monkeypatch.setenv("JIRA_PROJECT", "ABC")

    code = run(["ticket", str(payload), "--schema", str(schema), "--push"])
    assert code == 0
    assert capsys.readouterr().out.strip() == "created ABC-1"
    ticket = captured["ticket"]
    assert isinstance(ticket, Ticket)
    assert ticket.label.startswith("assay-schema-")
