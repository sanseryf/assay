"""Shared fixtures: a small ZIP reference database built from fixture data.

Tests never depend on the bundled 5 MB database; they build the same schema from a
handful of rows, so a GeoNames update cannot turn a test red for the wrong reason.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from assay.geo.build import build
from assay.geo.store import ZipStore

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


def _census_row(zip_code: str, county_fips: str, county: str, land: int) -> str:
    return (
        f"1|{zip_code}|ZCTA5 {zip_code}|1|0|G6350|B5|S|1|{county_fips}|{county}|"
        f"1|0|G4020|H1|A|{land}|0\n"
    )


@pytest.fixture(scope="session")
def geo_database(tmp_path_factory: pytest.TempPathFactory) -> Path:
    root = tmp_path_factory.mktemp("geo")
    geonames = root / "US.txt"
    geonames.write_text(GEONAMES_ROWS, encoding="utf-8")
    census = root / "zcta.txt"
    census.write_text(
        CENSUS_HEADER
        + _census_row("75071", "48085", "Collin County", 192028779)
        + _census_row("07030", "34017", "Hudson County", 5000000),
        encoding="utf-8",
    )
    out = root / "zips.sqlite"
    build(geonames, census, out)
    return out


@pytest.fixture()
def geo_store(geo_database: Path) -> ZipStore:
    store = ZipStore(geo_database)
    yield store
    store.close()
