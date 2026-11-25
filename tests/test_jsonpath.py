"""The JSONPath subset that rules are written in."""

from __future__ import annotations

import pytest

from assay.jsonpath import PathError, leaf_paths, parse, resolve, sibling


def test_parse_field_path() -> None:
    assert parse("$.members[*].dob") == ["members", "*", "dob"]


def test_parse_index_path() -> None:
    assert parse("$.members[0].dob") == ["members", 0, "dob"]


def test_parse_root() -> None:
    assert parse("$") == []


@pytest.mark.parametrize("bad", ["members[*]", "$.members[", "$.[0]", "$.a b"])
def test_bad_paths_raise(bad: str) -> None:
    with pytest.raises(PathError):
        parse(bad)


def test_resolve_wildcard_in_document_order() -> None:
    document = {"members": [{"dob": "1"}, {"dob": "2"}]}
    assert resolve(document, "$.members[*].dob") == [
        ("members[0].dob", "1"),
        ("members[1].dob", "2"),
    ]


def test_resolve_index() -> None:
    document = {"members": [{"dob": "1"}, {"dob": "2"}]}
    assert resolve(document, "$.members[1]") == [("members[1]", {"dob": "2"})]


def test_resolve_root_returns_the_document() -> None:
    document = {"a": 1}
    assert resolve(document, "$") == [("$", document)]


def test_missing_fields_resolve_to_nothing() -> None:
    assert resolve({"a": 1}, "$.b.c") == []


def test_wildcard_over_an_object_yields_its_values() -> None:
    document = {"tiers": {"a": "EMPLOYEE", "b": "employee"}}
    assert [value for _, value in resolve(document, "$.tiers[*]")] == [
        "EMPLOYEE",
        "employee",
    ]


def test_sibling_of_an_indexed_path() -> None:
    assert sibling("members[3].type", "relationship") == "members[3].relationship"


def test_sibling_of_a_root_field() -> None:
    assert sibling("type", "relationship") == "relationship"


def test_leaf_paths_skip_containers() -> None:
    document = {"a": 1, "b": {"c": [1, 2]}, "d": ["x"]}
    assert [path for path, _ in leaf_paths(document)] == ["a", "b.c[0]", "b.c[1]", "d[0]"]
