"""Fixtures: one broken payload per syntax detector, plus valid payloads.

The filenames say what is wrong, so a failure reads as "our BOM detection broke"
rather than "something in a 400-payload batch changed".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assay.syntax import analyze

FIXTURES = Path(__file__).parent / "fixtures"

EXPECTED_RULE = {
    "trailing-comma.json": "trailing-comma",
    "single-quotes.json": "single-quotes",
    "smart-quotes.json": "smart-quotes",
    "bom.json": "bom",
    "truncated.json": "truncation",
    "comments.json": "comments",
    "nan.json": "non-finite",
    "cp1252.json": "encoding",
}


@pytest.mark.parametrize(("filename", "rule"), sorted(EXPECTED_RULE.items()))
def test_each_broken_fixture_reports_its_cause(filename: str, rule: str) -> None:
    raw = (FIXTURES / "broken" / filename).read_bytes()
    result = analyze(raw, filename)
    rules = [finding.rule for finding in result.findings]
    assert rule in rules, f"{filename}: expected {rule}, got {rules}"


@pytest.mark.parametrize("path", sorted((FIXTURES / "valid").glob("*.json")))
def test_valid_fixtures_have_no_findings(path: Path) -> None:
    result = analyze(path.read_bytes(), path.name)
    assert result.ok
    assert result.findings == []


def test_bom_fixture_is_still_valid_after_the_bom() -> None:
    raw = (FIXTURES / "broken" / "bom.json").read_bytes()
    result = analyze(raw, "bom.json")
    assert result.ok and result.document is not None
    assert [finding.rule for finding in result.findings] == ["bom"]


def test_cp1252_fixture_is_not_valid_utf8() -> None:
    raw = (FIXTURES / "broken" / "cp1252.json").read_bytes()
    with pytest.raises(UnicodeDecodeError):
        raw.decode("utf-8")
    result = analyze(raw, "cp1252.json")
    assert "é" in (result.findings[0].hint or "") or "0xe9" in (result.findings[0].hint or "")
