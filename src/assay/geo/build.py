"""Build the bundled ZIP reference database.

Inputs are two public files:

* GeoNames ``US.zip`` (``US.txt`` inside): postal code, place name, state, county.
  Licensed CC BY 4.0; the attribution is written into the database and the README.
* The Census Bureau's ZCTA-to-county relationship file: land area per ZIP/county
  pair, which gives the county ratios and also covers ZCTAs with no place name.

Usage::

    python -m assay.geo.build --geonames US.txt --census tab20_zcta520_county20_natl.txt
"""

from __future__ import annotations

import argparse
import csv
import sqlite3
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .store import (
    STATE_BY_FIPS,
    ZipInfo,  # noqa: F401 - re-exported for callers of this module
    military_state,
    normalize_city,
    normalize_county,
)

SCHEMA = """
PRAGMA journal_mode = OFF;
PRAGMA synchronous = OFF;
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE zips (
    zip TEXT PRIMARY KEY,
    state TEXT NOT NULL,
    military TEXT,
    preferred_city TEXT
);
CREATE TABLE zip_cities (
    zip TEXT NOT NULL,
    city TEXT NOT NULL,
    preferred INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE zip_counties (
    zip TEXT NOT NULL,
    county TEXT NOT NULL,
    ratio REAL NOT NULL
);
CREATE INDEX idx_zip_cities_zip ON zip_cities(zip);
CREATE INDEX idx_zip_counties_zip ON zip_counties(zip);
"""

ATTRIBUTION = (
    "Postal codes: GeoNames (CC BY 4.0, https://www.geonames.org/). "
    "ZCTA-to-county areas: U.S. Census Bureau (public domain)."
)


@dataclass
class _Zip:
    zip: str
    state: str = ""
    state_source: str = ""
    cities: OrderedDict[str, str] = field(default_factory=OrderedDict)
    geonames_counties: OrderedDict[str, str] = field(default_factory=OrderedDict)
    census_land: dict[str, float] = field(default_factory=dict)
    census_counties: dict[str, str] = field(default_factory=dict)


def _read_geonames(path: Path, zips: dict[str, _Zip]) -> None:
    with path.open(encoding="utf-8", newline="") as handle:
        for line in handle:
            fields = line.rstrip("\n").split("\t")
            if len(fields) < 6:
                continue
            zip_code = fields[1].strip()
            if len(zip_code) != 5 or not zip_code.isdigit():
                continue
            entry = zips.setdefault(zip_code, _Zip(zip=zip_code))
            if not entry.state:
                entry.state = fields[4].strip().upper()
                entry.state_source = "geonames"
            city = fields[2].strip()
            if city:
                entry.cities.setdefault(normalize_city(city), city.upper())
            county = fields[5].strip()
            if county:
                entry.geonames_counties.setdefault(normalize_county(county), county)


def _read_census(path: Path, zips: dict[str, _Zip]) -> None:
    with path.open(encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="|")
        for row in reader:
            zip_code = (row.get("GEOID_ZCTA5_20") or "").strip()
            county = (row.get("NAMELSAD_COUNTY_20") or "").strip()
            if len(zip_code) != 5 or not zip_code.isdigit() or not county:
                continue
            try:
                land = float(row.get("AREALAND_PART") or 0)
            except ValueError:
                land = 0.0
            if land <= 0:
                continue
            entry = zips.setdefault(zip_code, _Zip(zip=zip_code))
            if not entry.state:
                fips = (row.get("GEOID_COUNTY_20") or "")[:2]
                entry.state = STATE_BY_FIPS.get(fips, "")
                entry.state_source = "census"
            key = normalize_county(county)
            entry.census_land[key] = entry.census_land.get(key, 0.0) + land
            entry.census_counties.setdefault(key, county)


def _counties(entry: _Zip) -> list[tuple[str, float]]:
    if entry.census_land:
        total = sum(entry.census_land.values())
        if total > 0:
            return [
                (entry.census_counties[key], land / total)
                for key, land in sorted(
                    entry.census_land.items(), key=lambda item: item[1], reverse=True
                )
            ]
    if entry.geonames_counties:
        share = 1.0 / len(entry.geonames_counties)
        return [(display, share) for display in entry.geonames_counties.values()]
    return []


def build(
    geonames: Path,
    census: Path | None,
    out: Path,
    *,
    built: str | None = None,
) -> dict[str, int]:
    """Read the source files, write the SQLite database, return row counts."""
    zips: dict[str, _Zip] = {}
    _read_geonames(geonames, zips)
    if census is not None:
        _read_census(census, zips)

    usable = {code: entry for code, entry in zips.items() if entry.state}
    out.parent.mkdir(parents=True, exist_ok=True)
    for stale in (out, out.with_suffix(".sqlite-journal")):
        stale.unlink(missing_ok=True)

    connection = sqlite3.connect(out)
    try:
        connection.executescript(SCHEMA)
        zip_rows: list[tuple[str, str, str | None, str | None]] = []
        city_rows: list[tuple[str, str, int]] = []
        county_rows: list[tuple[str, str, float]] = []
        for code, entry in sorted(usable.items()):
            preferred = next(iter(entry.cities.values()), None)
            zip_rows.append((code, entry.state, military_state(code), preferred))
            for index, display in enumerate(entry.cities.values()):
                city_rows.append((code, display, 1 if index == 0 else 0))
            for display, ratio in _counties(entry):
                county_rows.append((code, display, round(ratio, 6)))

        connection.executemany(
            "INSERT INTO zips (zip, state, military, preferred_city) VALUES (?, ?, ?, ?)",
            zip_rows,
        )
        connection.executemany(
            "INSERT INTO zip_cities (zip, city, preferred) VALUES (?, ?, ?)", city_rows
        )
        connection.executemany(
            "INSERT INTO zip_counties (zip, county, ratio) VALUES (?, ?, ?)", county_rows
        )
        meta = {
            "built": built or date.today().isoformat(),
            "attribution": ATTRIBUTION,
            "geonames": str(geonames.name),
            "census": str(census.name) if census else "",
            "zips": str(len(zip_rows)),
            "cities": str(len(city_rows)),
            "counties": str(len(county_rows)),
        }
        connection.executemany("INSERT INTO meta (key, value) VALUES (?, ?)", sorted(meta.items()))
        connection.commit()
    finally:
        connection.close()

    return {"zips": len(zip_rows), "cities": len(city_rows), "counties": len(county_rows)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--geonames", type=Path, required=True, help="GeoNames US.txt")
    parser.add_argument("--census", type=Path, help="Census ZCTA-county relationship file")
    parser.add_argument(
        "--out",
        type=Path,
        default=Path(__file__).parent / "data" / "zips.sqlite",
        help="Output SQLite file (defaults to the bundled package data)",
    )
    args = parser.parse_args(argv)

    counts = build(args.geonames, args.census, args.out)
    size = args.out.stat().st_size
    print(
        f"built {args.out} ({size / 1_048_576:.1f} MiB): "
        f"{counts['zips']:,} ZIPs, {counts['cities']:,} city names, "
        f"{counts['counties']:,} county rows"
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
