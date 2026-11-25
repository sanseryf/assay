"""Layer 5: ZIP, city, state, county and address checks."""

from __future__ import annotations

from typing import Any

from assay.geo.checks import GeoLayer
from assay.geo.store import ZipStore
from assay.models import Layer, Severity
from assay.pipeline import Pipeline
from assay.rules import RulesConfig


def address(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "line1": "123 Main St",
        "city": "MCKINNEY",
        "state": "TX",
        "zip": "75071",
    }
    base.update(overrides)
    return base


def layer(
    store: ZipStore,
    *,
    path: str = "$.members[*].home_address",
    residential: bool = True,
    line1_max: int | None = None,
    household_key: str | None = None,
    employer_address: str | None = None,
) -> GeoLayer:
    address_config: dict[str, Any] = {"path": path, "residential": residential}
    if line1_max is not None:
        address_config["line1_max"] = line1_max
    if household_key is not None:
        address_config["household_key"] = household_key
    config: dict[str, Any] = {"addresses": [address_config]}
    if employer_address is not None:
        config["employer_address"] = employer_address
    return GeoLayer(RulesConfig.model_validate({"geo": config}).geo, store)


def check(store: ZipStore, document: dict[str, Any], **kwargs: Any) -> list:
    return layer(store, **kwargs).check(document, "p1")


def payload(**overrides: Any) -> dict[str, Any]:
    return {"members": [{"home_address": address(**overrides)}]}


def rules(store: ZipStore, **kwargs: Any) -> RulesConfig:
    del store
    path = "$.members[*].home_address"
    return RulesConfig.model_validate({"geo": {"addresses": [{"path": path, **kwargs}]}})


def test_consistent_address_is_clean(geo_store: ZipStore) -> None:
    assert check(geo_store, payload()) == []


def test_four_digit_zip_suggests_the_leading_zero(geo_store: ZipStore) -> None:
    findings = check(geo_store, payload(zip="7030"))
    assert findings[0].rule == "zip-leading-zero"
    assert "07030" in (findings[0].hint or "")


def test_malformed_zip(geo_store: ZipStore) -> None:
    findings = check(geo_store, payload(zip="750711"))
    assert findings[0].rule == "zip-format"


def test_unknown_zip(geo_store: ZipStore) -> None:
    findings = check(geo_store, payload(zip="99999"))
    assert findings[0].rule == "zip-unknown"


def test_zip_state_mismatch_groups_by_state(geo_store: ZipStore) -> None:
    findings = check(geo_store, payload(state="OK"))
    finding = findings[0]
    assert finding.rule == "zip-state"
    assert finding.signature == "state:TX"
    assert "TX" in finding.message


def test_zip_city_mismatch_names_the_preferred_city(geo_store: ZipStore) -> None:
    findings = check(geo_store, payload(city="Dallas"))
    finding = findings[0]
    assert finding.rule == "zip-city"
    assert "MCKINNEY" in (finding.hint or "")


def test_acceptable_city_variant_is_information(geo_store: ZipStore) -> None:
    findings = check(geo_store, payload(zip="07030", city="Jersey City", state="NJ"))
    finding = findings[0]
    assert finding.rule == "city-alias"
    assert finding.severity is Severity.INFO
    assert "HOBOKEN" in (finding.hint or "")


def test_zip_county_mismatch(geo_store: ZipStore) -> None:
    findings = check(geo_store, payload(county="Dallas County"))
    assert findings[0].rule == "zip-county"
    assert "Collin County" in (findings[0].hint or "")


def test_county_spelling_variants_are_accepted(geo_store: ZipStore) -> None:
    assert check(geo_store, payload(county="collin")) == []


def test_full_state_name_is_suggested(geo_store: ZipStore) -> None:
    findings = check(geo_store, payload(state="Texas"))
    finding = findings[0]
    assert finding.rule == "state-code"
    assert "'TX'" in (finding.hint or "")


def test_military_zip_requires_apo_state(geo_store: ZipStore) -> None:
    findings = check(geo_store, payload(zip="09001", state="TX", city=""))
    assert findings[0].rule == "military-state"
    assert findings[0].severity is Severity.ERROR


def test_military_address_with_the_right_state_is_clean(geo_store: ZipStore) -> None:
    document = {
        "members": [
            {"home_address": address(line1="123 Main St", city="", state="AA", zip="09001")}
        ]
    }
    assert check(geo_store, document) == []


def test_po_box_is_not_a_residence(geo_store: ZipStore) -> None:
    findings = check(geo_store, {"members": [{"home_address": address(line1="PO Box 123")}]})
    assert findings[0].rule == "po-box"


def test_po_box_is_allowed_in_a_mailing_field(geo_store: ZipStore) -> None:
    document = {"employer": {"mailing_address": address(line1="PO Box 123")}}
    assert check(geo_store, document, path="$.employer.mailing_address", residential=False) == []


def test_missing_street_number(geo_store: ZipStore) -> None:
    findings = check(geo_store, {"members": [{"home_address": address(line1="Main St")}]})
    assert findings[0].rule == "missing-street-number"


def test_unit_in_line_one(geo_store: ZipStore) -> None:
    findings = check(geo_store, {"members": [{"home_address": address(line1="123 Main St Apt 4")}]})
    assert findings[0].rule == "unit-in-line1"


def test_unit_is_fine_when_line_two_has_it(geo_store: ZipStore) -> None:
    document = {"members": [{"home_address": address(line1="123 Main St", line2="Apt 4")}]}
    assert check(geo_store, document) == []


def test_line1_length_limit(geo_store: ZipStore) -> None:
    findings = check(geo_store, payload(line1="123 Very Long Street Name"), line1_max=10)
    assert findings[0].rule == "line1-too-long"


def test_city_and_state_swapped(geo_store: ZipStore) -> None:
    findings = check(geo_store, payload(city="TX", state="McKinney"))
    rules_found = {finding.rule for finding in findings}
    assert "city-state-swapped" in rules_found


def test_home_address_equal_to_the_employer(geo_store: ZipStore) -> None:
    shared = address()
    document = {
        "employer": {"mailing_address": shared},
        "members": [{"home_address": shared}],
    }
    findings = check(geo_store, document, employer_address="$.employer.mailing_address")
    assert findings[0].rule == "home-equals-employer"


def test_household_members_with_different_addresses(geo_store: ZipStore) -> None:
    geo = layer(geo_store, household_key="$.members[*].household_id")
    document = {
        "members": [
            {"household_id": "H1", "home_address": address()},
            {"household_id": "H1", "home_address": address(line1="9 Other Rd")},
        ]
    }
    findings = geo.check(document, "p1")
    findings.extend(geo.finalize())
    assert any(finding.rule == "household-address" for finding in findings)


def test_many_mismatches_with_one_state_are_a_defaulted_state(geo_store: ZipStore) -> None:
    geo = layer(geo_store)
    for index in range(5):
        document = payload(state="OK")
        geo.check(document, f"p{index}")
    findings = geo.finalize()
    assert len(findings) == 5
    assert all(finding.rule == "defaulted-state" for finding in findings)


def test_pipeline_runs_the_geo_layer(geo_store: ZipStore) -> None:
    from assay.loader import Payload

    config = rules(geo_store)
    report = Pipeline(rules=config, geo_store=geo_store).run(
        [
            Payload(
                "p1",
                b'{"members": [{"home_address": {"line1": "123 Main St", '
                b'"city": "Dallas", "state": "TX", "zip": "75071"}}]}',
            )
        ]
    )
    assert report.groups[0].layer is Layer.GEO
    assert report.exit_code == 2
