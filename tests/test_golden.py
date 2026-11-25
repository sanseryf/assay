"""Golden file: fifteen fixtures, one report snapshot, every message and hint pinned.

A change in wording, ordering or grouping shows up as a readable diff instead of
being noticed by a user. Regenerate deliberately:

    UPDATE_GOLDEN=1 python -m pytest tests/test_golden.py
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from assay.loader import Payload
from assay.models import Report
from assay.pipeline import Pipeline

FIXTURES = Path(__file__).parent / "fixtures"
SCHEMA_PATH = Path(__file__).parent.parent / "examples" / "member.schema.json"
EXPECTED = FIXTURES / "expected.json"

FOLDERS = ("valid", "invalid", "broken")


def build_report() -> Report:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    paths = [path for folder in FOLDERS for path in sorted((FIXTURES / folder).glob("*.json"))]
    payloads = [
        Payload(payload_id=path.relative_to(FIXTURES).as_posix(), raw=path.read_bytes())
        for path in paths
    ]
    return Pipeline(schema=schema).run(payloads)


def test_golden_report_matches_snapshot() -> None:
    actual = build_report().to_dict()
    actual.pop("duration_ms")

    if os.environ.get("UPDATE_GOLDEN"):
        EXPECTED.write_text(
            json.dumps(actual, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    expected = json.loads(EXPECTED.read_text(encoding="utf-8"))
    expected.pop("duration_ms", None)
    assert actual == expected


def test_fixture_batch_is_complete() -> None:
    report = build_report()
    assert report.payloads_scanned == 15
    assert report.payloads_clean == 2
    assert report.exit_code == 2


def test_one_group_per_root_cause() -> None:
    report = build_report()
    # Eight syntax causes, five schema causes, two clean payloads: thirteen groups,
    # not fifteen findings-per-payload rows.
    assert len(report.groups) == 13
    assert len({group.fingerprint for group in report.groups}) == 13


def test_a_syntax_failure_does_not_leak_into_schema_findings() -> None:
    report = build_report()
    for group in report.groups:
        if group.path == "$":
            assert group.layer.value == "syntax"
            assert group.payloads_affected == 1


def test_clean_payloads_are_counted_once() -> None:
    report = build_report()
    affected = {payload_id for group in report.groups for payload_id in group.payload_ids}
    assert "valid/minimal.json" not in affected
    assert "valid/full.json" not in affected
