"""Rule files: a typo must fail loudly at startup."""

from __future__ import annotations

from pathlib import Path

import pytest

from assay.rules import ConfigError, RulesConfig, load_config, rules_by_kind


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "rules.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def test_empty_file_is_a_valid_empty_config(tmp_path: Path) -> None:
    config = load_config(write(tmp_path, ""))
    assert config.rules == ()
    assert config.duplicate_keys is True
    assert config.strings.enabled is True


def test_compare_rule_loads(tmp_path: Path) -> None:
    config = load_config(
        write(
            tmp_path,
            """
            version: 1
            rules:
              - id: dates-ordered
                kind: compare
                left: "$.members[*].start_date"
                op: "<="
                right: "$.members[*].end_date"
                severity: error
                hint: "End date precedes start date."
            """,
        )
    )
    assert len(config.rules) == 1
    rule = config.rules[0]
    assert rule.id == "dates-ordered"
    assert rule.kind == "compare"
    assert rule.severity.value == "error"


def test_severity_is_case_insensitive(tmp_path: Path) -> None:
    config = load_config(
        write(
            tmp_path,
            """
            rules:
              - id: placeholder-name
                kind: placeholder
                severity: WARNING
            """,
        )
    )
    assert config.rules[0].severity.value == "warning"


def test_unknown_rule_kind_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="kind"):
        load_config(write(tmp_path, "rules:\n  - id: x\n    kind: nope\n"))


def test_unknown_field_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="compar"):
        load_config(
            write(
                tmp_path,
                """
                rules:
                  - id: x
                    kind: compare
                    left: "$.a"
                    lefty: "$.b"
                    op: "<="
                    right: "$.c"
                """,
            )
        )


def test_bad_path_expression_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="must start with"):
        load_config(
            write(
                tmp_path,
                """
                rules:
                  - id: x
                    kind: compare
                    left: "members[*].a"
                    op: "<="
                    right: "$.b"
                """,
            )
        )


def test_duplicate_rule_ids_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="unique"):
        load_config(
            write(
                tmp_path,
                """
                rules:
                  - id: same
                    kind: placeholder
                  - id: same
                    kind: placeholder
                """,
            )
        )


def test_threshold_out_of_range_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="threshold"):
        load_config(
            write(
                tmp_path,
                """
                rules:
                  - id: constant
                    kind: constant_smell
                    path: "$.members[*].hire_date"
                    threshold: 1.5
                """,
            )
        )


def test_invalid_yaml_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not valid YAML"):
        load_config(write(tmp_path, "rules: [unclosed"))


def test_missing_file_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.yaml")


def test_strings_section_validates_paths(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="must start with"):
        load_config(write(tmp_path, "strings:\n  max_length:\n    first_name: 40\n"))


def test_rules_by_kind_filters(tmp_path: Path) -> None:
    config = load_config(
        write(
            tmp_path,
            """
            rules:
              - id: a
                kind: placeholder
              - id: b
                kind: case_drift
                path: "$.members[*].tier"
            """,
        )
    )
    assert [rule.id for rule in rules_by_kind(config, "placeholder")] == ["a"]
    assert [rule.id for rule in rules_by_kind(config, "case_drift")] == ["b"]


def test_default_config_is_reusable() -> None:
    assert RulesConfig().rules == ()
