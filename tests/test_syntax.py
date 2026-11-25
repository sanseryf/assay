"""Layer 1: every detector, plus the false positives that would make it useless."""

from __future__ import annotations

import json

import pytest

from assay.syntax import BOM, analyze


def rules(result) -> list[str]:
    return [finding.rule for finding in result.findings]


def test_valid_payload_passes() -> None:
    result = analyze(b'{"a": 1}', "p1")
    assert result.ok
    assert result.document == {"a": 1}
    assert result.findings == []


def test_json_null_is_a_valid_document() -> None:
    result = analyze(b"null", "p1")
    assert result.ok
    assert result.document is None


@pytest.mark.parametrize(
    ("raw", "rule", "line", "col"),
    [
        (b'{"a": 1,}', "trailing-comma", 1, 8),
        (b"[1, 2, ]", "trailing-comma", 1, 6),
        (b"{'a': 1}", "single-quotes", 1, 2),
        ("{\u201ca\u201d: 1}".encode(), "smart-quotes", 1, 2),
        (b'{"a": 1 // hi\n}', "comments", 1, 9),
        (b'{"a": 1 /* hi */}', "comments", 1, 9),
        (b'{"x": NaN}', "non-finite", 1, 7),
        (b'{"x": -Infinity}', "non-finite", 1, 7),
        (b'{"a": [1, 2', "truncation", 1, 7),
        (b'{"a": "foo', "truncation", 1, 7),
        (b'{"a": 1]', "mismatched-bracket", 1, 8),
    ],
)
def test_detector_reports_cause_and_position(raw: bytes, rule: str, line: int, col: int) -> None:
    result = analyze(raw, "p1")
    assert not result.ok
    matching = [finding for finding in result.findings if finding.rule == rule]
    assert matching, f"expected {rule}, got {rules(result)}"
    finding = matching[0]
    assert (finding.line, finding.col) == (line, col)


def test_unescaped_newline_in_string() -> None:
    result = analyze(b'{"a": "line one\nline two"}', "p1")
    assert "unescaped-newline" in rules(result)
    finding = next(f for f in result.findings if f.rule == "unescaped-newline")
    assert finding.line == 1


def test_bom_is_reported_and_recovered() -> None:
    result = analyze(b'\xef\xbb\xbf{"a": 1}', "p1")
    assert result.ok, "a BOM is recoverable, so later layers should still run"
    assert result.document == {"a": 1}
    assert rules(result) == ["bom"]
    assert result.findings[0].line == 1 and result.findings[0].col == 1


def test_encoding_failure_names_the_byte() -> None:
    result = analyze(b'{"name": "Jos\xe9"}', "p1")
    assert not result.ok
    assert result.text is None
    finding = result.findings[0]
    assert finding.rule == "encoding"
    assert (finding.line, finding.col) == (1, 14)
    assert "0xe9" in (finding.hint or "")
    assert "cp1252" in (finding.hint or "")


def test_generic_fallback_when_no_detector_matches() -> None:
    result = analyze(b'{"a": }', "p1")
    assert not result.ok
    assert rules(result) == ["invalid-json"]
    assert "invalid JSON" in result.findings[0].message


def test_empty_input_is_a_syntax_error() -> None:
    result = analyze(b"", "p1")
    assert not result.ok
    assert rules(result) == ["invalid-json"]


def test_multiple_causes_are_all_reported_in_text_order() -> None:
    result = analyze(b"{'a': 'x',}", "p1")
    assert rules(result) == ["single-quotes", "trailing-comma"]
    assert [(f.line, f.col) for f in result.findings] == [(1, 2), (1, 10)]


def test_no_false_positives_from_content_inside_strings() -> None:
    # This payload is broken at the very end, so every detector runs. Nothing that
    # lives inside a string may be blamed: URLs contain //, names contain '.
    raw = b'{"url": "http://x//y", "name": "O\'Brien", "note": "NaN", "oops": }'
    result = analyze(raw, "p1")
    assert not result.ok
    found = set(rules(result))
    assert found == {"invalid-json"}
    assert "comments" not in found
    assert "single-quotes" not in found
    assert "non-finite" not in found


def test_smart_quotes_inside_a_quoted_string_are_content_not_syntax() -> None:
    raw = '{"note": "he said \u201chi\u201d", "oops": }'.encode()
    result = analyze(raw, "p1")
    assert "smart-quotes" not in rules(result)


def test_deep_nesting_is_reported_not_raised() -> None:
    result = analyze(b"[" * 3000 + b"]" * 3000, "p1")
    assert not result.ok
    assert "input-too-deep" in rules(result)


def test_bom_constant_is_the_character_not_the_mojibake() -> None:
    assert BOM == "\ufeff"
    assert analyze(BOM.encode() + b"{}", "p1").ok


def test_valid_json_with_awkward_values_round_trips() -> None:
    document = {
        "url": "http://x//y",
        "name": "O'Brien",
        "note": "NaN",
        "list": ["a,]b", "c"],
        "text": "line\nbreak",
        "empty": "",
    }
    result = analyze(json.dumps(document).encode(), "p1")
    assert result.ok
    assert result.document == document
