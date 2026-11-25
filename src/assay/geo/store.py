"""The offline ZIP reference: one SQLite file, read-only.

Geography is the one layer with reference data, and it ships with Assay: a lookup
must never depend on a network call, a rate limit or a service that is down. The
database is built from GeoNames postal codes (CC BY 4.0) and the Census Bureau's
ZCTA-to-county relationship file (public domain); ``assay.geo.build`` rebuilds it.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

ZIP_DB_FILENAME = "zips.sqlite"

# Census state FIPS codes, for the ZIPs that exist in the ZCTA file but have no
# GeoNames place record (usually unincorporated land or PO-box-only areas).
STATE_BY_FIPS: dict[str, str] = {
    "01": "AL",
    "02": "AK",
    "04": "AZ",
    "05": "AR",
    "06": "CA",
    "08": "CO",
    "09": "CT",
    "10": "DE",
    "11": "DC",
    "12": "FL",
    "13": "GA",
    "15": "HI",
    "16": "ID",
    "17": "IL",
    "18": "IN",
    "19": "IA",
    "20": "KS",
    "21": "KY",
    "22": "LA",
    "23": "ME",
    "24": "MD",
    "25": "MA",
    "26": "MI",
    "27": "MN",
    "28": "MS",
    "29": "MO",
    "30": "MT",
    "31": "NE",
    "32": "NV",
    "33": "NH",
    "34": "NJ",
    "35": "NM",
    "36": "NY",
    "37": "NC",
    "38": "ND",
    "39": "OH",
    "40": "OK",
    "41": "OR",
    "42": "PA",
    "44": "RI",
    "45": "SC",
    "46": "SD",
    "47": "TN",
    "48": "TX",
    "49": "UT",
    "50": "VT",
    "51": "VA",
    "53": "WA",
    "54": "WV",
    "55": "WI",
    "56": "WY",
    "60": "AS",
    "66": "GU",
    "69": "MP",
    "72": "PR",
    "78": "VI",
}

# Military ZIP prefixes: (low, high, state the address must use).
MILITARY_RANGES: tuple[tuple[str, str, str], ...] = (
    ("090", "098", "AA"),
    ("340", "340", "AE"),
    ("962", "966", "AP"),
)

_COUNTY_SUFFIXES = (
    " CENSUS AREA",
    " MUNICIPALITY",
    " CITY AND BOROUGH",
    " BOROUGH",
    " PARISH",
    " COUNTY",
    " CITY",
)


class GeoDataError(Exception):
    """The bundled reference database is missing or unreadable."""


def normalize_city(value: str) -> str:
    """Compare cities without punctuation or extra whitespace: ``Mc Kinney`` == ``MCKINNEY``."""
    return " ".join(value.upper().replace(".", " ").replace(",", " ").split())


def normalize_county(value: str) -> str:
    """``Collin County`` and ``COLLIN`` are the same county for matching."""
    text = " ".join(value.upper().replace(".", " ").replace(",", " ").split())
    for suffix in _COUNTY_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)].strip()
            break
    return text


def military_state(zip_code: str) -> str | None:
    """The APO/FPO/DPO state a military ZIP must be used with, if any."""
    prefix = zip_code[:3]
    for low, high, state in MILITARY_RANGES:
        if low <= prefix <= high:
            return state
    return None


@dataclass(frozen=True, slots=True)
class ZipInfo:
    """What the reference database knows about one five-digit ZIP."""

    zip: str
    state: str
    military: str | None
    preferred_city: str | None
    cities: tuple[str, ...]
    counties: tuple[tuple[str, float], ...]

    @property
    def preferred_city_display(self) -> str | None:
        return self.preferred_city.upper() if self.preferred_city else None


def default_database_path() -> Path:
    """Where the SQLite file ships inside the installed package."""
    return Path(str(files("assay.geo").joinpath("data", ZIP_DB_FILENAME)))


class ZipStore:
    """Read-only lookups against the bundled database, with a small memo.

    A batch repeats the same handful of ZIPs thousands of times; caching the row per
    ZIP keeps schema and boundary checks cheap without holding the whole table.
    """

    def __init__(self, path: Path | None = None, *, max_cache: int = 20_000) -> None:
        self.path = path or default_database_path()
        if not self.path.exists():
            raise GeoDataError(
                f"ZIP reference database not found at {self.path}; rebuild it with "
                "`python -m assay.geo.build`"
            )
        self._connection = sqlite3.connect(f"file:{self.path.as_posix()}?mode=ro", uri=True)
        self._connection.row_factory = sqlite3.Row
        self._cache: dict[str, ZipInfo | None] = {}
        self._max_cache = max_cache

    def lookup(self, zip_code: str) -> ZipInfo | None:
        """The record for a five-digit ZIP, or ``None`` when it is not in the reference."""
        if zip_code in self._cache:
            return self._cache[zip_code]

        row = self._connection.execute(
            "SELECT zip, state, military, preferred_city FROM zips WHERE zip = ?",
            (zip_code,),
        ).fetchone()
        info: ZipInfo | None = None
        if row is not None:
            cities = tuple(
                item["city"]
                for item in self._connection.execute(
                    "SELECT city FROM zip_cities WHERE zip = ? ORDER BY preferred DESC, city",
                    (zip_code,),
                )
            )
            counties = tuple(
                (item["county"], item["ratio"])
                for item in self._connection.execute(
                    "SELECT county, ratio FROM zip_counties WHERE zip = ? ORDER BY ratio DESC",
                    (zip_code,),
                )
            )
            info = ZipInfo(
                zip=row["zip"],
                state=row["state"],
                military=row["military"],
                preferred_city=row["preferred_city"],
                cities=cities,
                counties=counties,
            )

        if len(self._cache) >= self._max_cache:
            self._cache.clear()
        self._cache[zip_code] = info
        return info

    def close(self) -> None:
        self._connection.close()

    def __enter__(self) -> ZipStore:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()
