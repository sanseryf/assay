"""Properties that must hold for every input, not just the examples we thought of.

If a detector ever starts blaming text inside a valid string, or a trailing comma
ever slips through, these tests find it without a new fixture.
"""

from __future__ import annotations

import json

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from assay.syntax import analyze

# Hypothesis' own JSON strategy exists, but it generates pathological documents
# (huge floats, deep nesting) that json.dumps cannot round-trip here.
_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-(10**12), max_value=10**12),
    st.text(max_size=40),
)
_json = st.recursive(
    _scalars,
    lambda children: st.one_of(
        st.lists(children, max_size=5),
        st.dictionaries(st.text(max_size=12), children, max_size=5),
    ),
    max_leaves=20,
)


@settings(max_examples=120, suppress_health_check=[HealthCheck.too_slow])
@given(_json)
def test_valid_json_never_produces_findings(value: object) -> None:
    raw = json.dumps(value, ensure_ascii=False).encode()
    result = analyze(raw, "p")
    assert result.ok, f"failed to parse its own output: {raw!r}"
    assert result.findings == []


@settings(max_examples=100)
@given(st.dictionaries(st.text(min_size=1, max_size=12), st.integers(), min_size=1))
def test_trailing_comma_is_always_found(document: dict[str, int]) -> None:
    raw = json.dumps(document)
    assert raw.endswith("}")
    broken = json.dumps(document)[:-1] + ",}"
    result = analyze(broken.encode(), "p")
    assert not result.ok
    assert "trailing-comma" in [finding.rule for finding in result.findings]


@settings(max_examples=100)
@given(st.binary(max_size=200))
def test_arbitrary_bytes_never_raise(raw: bytes) -> None:
    result = analyze(raw, "p")
    assert isinstance(result.ok, bool)
    for finding in result.findings:
        assert finding.layer.value == "syntax"
        assert finding.path == "$"


@settings(max_examples=60)
@given(st.lists(st.integers(), max_size=6))
def test_truncation_is_found_at_every_length(values: list[int]) -> None:
    if not values:
        return
    raw = json.dumps(values)
    for cut in range(1, len(raw)):
        result = analyze(raw[:cut].encode(), "p")
        assert not result.ok, f"prefix {raw[:cut]!r} unexpectedly parsed"
        assert "truncation" in [finding.rule for finding in result.findings]
