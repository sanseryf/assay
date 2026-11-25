"""Building the bundled ZIP reference from public files."""

from __future__ import annotations

from pathlib import Path

from assay.geo.build import build
from assay.geo.store import ZipStore, military_state

CENSUS_HEADER = (
    "OID_ZCTA5_20|GEOID_ZCTA5_20|NAMELSAD_ZCTA5_20|AREALAND_ZCTA5_20|AREAWATER_ZCTA5_20|"
    "MTFCC_ZCTA5_20|CLASSFP_ZCTA5_20|FUNCSTAT_ZCTA5_20|OID_COUNTY_20|GEOID_COUNTY_20|"
    "NAMELSAD_COUNTY_20|AREALAND_COUNTY_20|AREAWATER_COUNTY_20|MTFCC_COUNTY_20|"
    "CLASSFP_COUNTY_20|FUNCSTAT_COUNTY_20|AREALAND_PART|AREAWATER_PART\n"
)

GEONAMES_ROWS = """US\t75071\tMckinney\tTexas\tTX\tCollin\t085\t\t\t33.19\t-96.61\t4
US\t07030\tHoboken\tNew Jersey\tNJ\tHudson\t017\t\t\t40.74\t-74.03\t4
US\t07030\tJersey City\tNew Jersey\tNJ\tHudson\t017\t\t\t40.73\t-74.04\t4
"""


def census_row(zip_code: str, county_fips: str, county: str, land: int) -> str:
    return (
        f"1|{zip_code}|ZCTA5 {zip_code}|1|0|G6350|B5|S|1|{county_fips}|{county}|"
        f"1|0|G4020|H1|A|{land}|0\n"
    )


def make_sources(tmp_path: Path) -> tuple[Path, Path]:
    geonames = tmp_path / "US.txt"
    geonames.write_text(GEONAMES_ROWS, encoding="utf-8")
    census = tmp_path / "zcta.txt"
    census.write_text(
        CENSUS_HEADER
        + census_row("75071", "48085", "Collin County", 192028779)
        + census_row("07030", "34017", "Hudson County", 5000000)
        + census_row("90001", "06037", "Los Angeles County", 8000000),
        encoding="utf-8",
    )
    return geonames, census


def test_build_reads_both_sources(tmp_path: Path) -> None:
    geonames, census = make_sources(tmp_path)
    out = tmp_path / "zips.sqlite"
    counts = build(geonames, census, out)
    assert counts == {"zips": 3, "cities": 3, "counties": 3}
    assert out.exists()


def test_geonames_zip_has_state_city_and_county(tmp_path: Path) -> None:
    geonames, census = make_sources(tmp_path)
    out = tmp_path / "zips.sqlite"
    build(geonames, census, out)
    with ZipStore(out) as store:
        info = store.lookup("75071")
    assert info is not None
    assert info.state == "TX"
    assert info.preferred_city == "MCKINNEY"
    assert info.cities == ("MCKINNEY",)
    assert info.counties == (("Collin County", 1.0),)


def test_census_only_zip_still_has_a_state_and_county(tmp_path: Path) -> None:
    geonames, census = make_sources(tmp_path)
    out = tmp_path / "zips.sqlite"
    build(geonames, census, out)
    with ZipStore(out) as store:
        info = store.lookup("90001")
    assert info is not None
    assert info.state == "CA"  # from the county FIPS, not GeoNames
    assert info.preferred_city is None
    assert info.counties == (("Los Angeles County", 1.0),)


def test_preferred_city_is_the_first_geonames_row(tmp_path: Path) -> None:
    geonames, census = make_sources(tmp_path)
    out = tmp_path / "zips.sqlite"
    build(geonames, census, out)
    with ZipStore(out) as store:
        info = store.lookup("07030")
    assert info is not None
    assert info.preferred_city == "HOBOKEN"
    assert set(info.cities) == {"HOBOKEN", "JERSEY CITY"}


def test_county_ratios_come_from_census_land_area(tmp_path: Path) -> None:
    geonames = tmp_path / "US.txt"
    geonames.write_text("US\t75071\tMckinney\tTexas\tTX\tCollin\t085\t\t\t1\t1\t4\n")
    census = tmp_path / "zcta.txt"
    census.write_text(
        CENSUS_HEADER
        + census_row("75071", "48085", "Collin County", 75)
        + census_row("75071", "48121", "Denton County", 25),
        encoding="utf-8",
    )
    out = tmp_path / "zips.sqlite"
    build(geonames, census, out)
    with ZipStore(out) as store:
        info = store.lookup("75071")
    assert info is not None
    assert info.counties[0][0] == "Collin County"
    assert round(info.counties[0][1], 2) == 0.75
    assert round(info.counties[1][1], 2) == 0.25


def test_military_zips_are_recognised_without_a_record(tmp_path: Path) -> None:
    assert military_state("09001") == "AA"
    assert military_state("34001") == "AE"
    assert military_state("96201") == "AP"
    assert military_state("75071") is None


def test_missing_database_is_a_clear_error(tmp_path: Path) -> None:
    from assay.geo.store import GeoDataError

    try:
        ZipStore(tmp_path / "nope.sqlite")
    except GeoDataError as exc:
        assert "rebuild" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected GeoDataError")
