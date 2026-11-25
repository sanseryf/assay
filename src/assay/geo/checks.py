"""Layer 5: does the ZIP, city, state and address agree with reality?

Downstream systems reject records whose ZIP, city and state do not agree, or whose
"home address" is a mailbox. Those payloads pass every schema check because
``"75071"``, ``"Dallas"`` and ``"OK"`` are each valid strings. This layer compares
them with the bundled reference data and with each other.

Everything here is offline: no address leaves the machine.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from typing import Any, cast

import usaddress

from ..hints import closest_match, preview
from ..jsonpath import resolve
from ..models import Finding, Layer, Severity
from ..paths import join
from ..rules import GeoAddressConfig, GeoConfig
from .store import ZipInfo, ZipStore, military_state, normalize_city, normalize_county

US_STATES = frozenset(
    {
        "AL",
        "AK",
        "AZ",
        "AR",
        "CA",
        "CO",
        "CT",
        "DE",
        "DC",
        "FL",
        "GA",
        "HI",
        "ID",
        "IL",
        "IN",
        "IA",
        "KS",
        "KY",
        "LA",
        "ME",
        "MD",
        "MA",
        "MI",
        "MN",
        "MS",
        "MO",
        "MT",
        "NE",
        "NV",
        "NH",
        "NJ",
        "NM",
        "NY",
        "NC",
        "ND",
        "OH",
        "OK",
        "OR",
        "PA",
        "RI",
        "SC",
        "SD",
        "TN",
        "TX",
        "UT",
        "VT",
        "VA",
        "WA",
        "WV",
        "WI",
        "WY",
        "AS",
        "GU",
        "MP",
        "PR",
        "VI",
        "AA",
        "AE",
        "AP",
    }
)
MILITARY_STATES = frozenset({"AA", "AE", "AP"})

STATE_NAMES: dict[str, str] = {
    "ALABAMA": "AL",
    "ALASKA": "AK",
    "ARIZONA": "AZ",
    "ARKANSAS": "AR",
    "CALIFORNIA": "CA",
    "COLORADO": "CO",
    "CONNECTICUT": "CT",
    "DELAWARE": "DE",
    "DISTRICT OF COLUMBIA": "DC",
    "FLORIDA": "FL",
    "GEORGIA": "GA",
    "HAWAII": "HI",
    "IDAHO": "ID",
    "ILLINOIS": "IL",
    "INDIANA": "IN",
    "IOWA": "IA",
    "KANSAS": "KS",
    "KENTUCKY": "KY",
    "LOUISIANA": "LA",
    "MAINE": "ME",
    "MARYLAND": "MD",
    "MASSACHUSETTS": "MA",
    "MICHIGAN": "MI",
    "MINNESOTA": "MN",
    "MISSISSIPPI": "MS",
    "MISSOURI": "MO",
    "MONTANA": "MT",
    "NEBRASKA": "NE",
    "NEVADA": "NV",
    "NEW HAMPSHIRE": "NH",
    "NEW JERSEY": "NJ",
    "NEW MEXICO": "NM",
    "NEW YORK": "NY",
    "NORTH CAROLINA": "NC",
    "NORTH DAKOTA": "ND",
    "OHIO": "OH",
    "OKLAHOMA": "OK",
    "OREGON": "OR",
    "PENNSYLVANIA": "PA",
    "RHODE ISLAND": "RI",
    "SOUTH CAROLINA": "SC",
    "SOUTH DAKOTA": "SD",
    "TENNESSEE": "TN",
    "TEXAS": "TX",
    "UTAH": "UT",
    "VERMONT": "VT",
    "VIRGINIA": "VA",
    "WASHINGTON": "WA",
    "WEST VIRGINIA": "WV",
    "WISCONSIN": "WI",
    "WYOMING": "WY",
    "PUERTO RICO": "PR",
    "GUAM": "GU",
    "AMERICAN SAMOA": "AS",
    "NORTHERN MARIANA ISLANDS": "MP",
    "VIRGIN ISLANDS": "VI",
}

_ZIP = re.compile(r"^(\d{5})(?:-(\d{4}))?$")
_UNIT = re.compile(
    r"\b(?:APT|APARTMENT|STE|SUITE|UNIT|BLDG|BUILDING|FL|FLOOR|ROOM|RM)\.?\s*#?\s*\w+",
    re.IGNORECASE,
)
_PO_BOX = re.compile(r"\b(?:P\.?\s?O\.?\s*BOX|POST\s+OFFICE\s+BOX|PMB)\b", re.IGNORECASE)

Mismatch = tuple[str, str, str]


class GeoLayer:
    """Checks configured address objects and remembers what the batch needs."""

    def __init__(self, config: GeoConfig, store: ZipStore | None = None) -> None:
        self.config = config
        self._store = store
        self._defaulted: list[Mismatch] = []
        self._households: dict[str, list[tuple[str, str, str]]] = defaultdict(list)

    @property
    def store(self) -> ZipStore:
        if self._store is None:
            self._store = ZipStore()
        return self._store

    def check(self, document: Any, payload_id: str) -> list[Finding]:
        findings: list[Finding] = []
        employer = self._employer(document)
        for address in self.config.addresses:
            for path, value in resolve(document, address.path):
                if not isinstance(value, dict):
                    continue
                findings.extend(self._address(path, value, address, payload_id))
                if self.config.employer_address:
                    findings.extend(
                        self._employer_check(path, value, employer, address, payload_id)
                    )
                if address.household_key:
                    self._remember_household(document, path, value, address, payload_id)
        return findings

    # ------------------------------------------------------------------ addresses

    def _address(
        self, path: str, value: dict[str, Any], config: GeoAddressConfig, payload_id: str
    ) -> list[Finding]:
        fields = _fields(value)
        line1 = _text(fields, "line1")
        line2 = _text(fields, "line2")
        city = _text(fields, "city")
        state = _text(fields, "state").upper()
        county = _text(fields, "county")
        raw_zip = _text(fields, "zip")
        findings: list[Finding] = []

        if line1:
            findings.extend(_line1_checks(path, line1, line2, config, payload_id))

        info: ZipInfo | None = None
        if raw_zip:
            zip_findings, info, zip5 = self._zip_checks(path, raw_zip, payload_id)
            findings.extend(zip_findings)
            if info is not None or military_state(zip5):
                findings.extend(
                    self._state_and_place_checks(path, info, zip5, city, state, county, payload_id)
                )
            if state and zip5:
                expected = military_state(zip5) or (info.state if info else None)
                if expected and expected != state:
                    self._defaulted.append((payload_id, join(path, "state"), state))

        if city and state and city.upper() in US_STATES and state not in US_STATES:
            findings.append(
                self._finding(
                    "city-state-swapped",
                    join(path, "state"),
                    "city and state look swapped",
                    f"did you mean city {state!r} and state {city.upper()!r}?",
                    payload_id,
                    severity=Severity.WARNING,
                )
            )
        return findings

    def _zip_checks(
        self, path: str, raw: str, payload_id: str
    ) -> tuple[list[Finding], ZipInfo | None, str]:
        match = _ZIP.match(raw)
        if match is None:
            if re.fullmatch(r"\d{4}", raw):
                suggested = raw.zfill(5)
                hint = (
                    f"likely {suggested!r}: a leading zero disappears when a ZIP is stored "
                    "as a number."
                )
                return (
                    [
                        self._finding(
                            "zip-leading-zero",
                            join(path, "zip"),
                            "four-digit ZIP",
                            hint,
                            payload_id,
                            preview_value=raw,
                        )
                    ],
                    None,
                    "",
                )
            hint = "expect ##### or #####-####."
            return (
                [
                    self._finding(
                        "zip-format",
                        join(path, "zip"),
                        "invalid ZIP format",
                        hint,
                        payload_id,
                        preview_value=raw,
                    )
                ],
                None,
                "",
            )

        zip5 = match.group(1)
        info = self.store.lookup(zip5)
        if info is None and military_state(zip5) is None:
            return (
                [
                    self._finding(
                        "zip-unknown",
                        join(path, "zip"),
                        "ZIP is not in the reference data",
                        "check the digits; the bundled table covers every US ZIP in GeoNames.",
                        payload_id,
                        preview_value=zip5,
                    )
                ],
                None,
                zip5,
            )
        return [], info, zip5

    def _state_and_place_checks(
        self,
        path: str,
        info: ZipInfo | None,
        zip5: str,
        city: str,
        state: str,
        county: str,
        payload_id: str,
    ) -> list[Finding]:
        findings: list[Finding] = []
        expected_military = military_state(zip5)

        if state and state not in US_STATES:
            suggestion = STATE_NAMES.get(state) or closest_match(state, US_STATES)
            hint = f"did you mean {suggestion!r}?" if suggestion else "use the two-letter code."
            findings.append(
                self._finding(
                    "state-code",
                    join(path, "state"),
                    "not a two-letter state code",
                    hint,
                    payload_id,
                    preview_value=state,
                )
            )
            return findings  # a full state name makes the rest pure noise

        if expected_military:
            if state and state != expected_military:
                findings.append(
                    self._finding(
                        "military-state",
                        join(path, "state"),
                        f"military ZIP belongs to {expected_military}",
                        f"APO/FPO/DPO addresses use {expected_military}, not {state}.",
                        payload_id,
                        signature=f"expected:{expected_military}",
                        preview_value=state,
                    )
                )
            return findings

        if info is None:
            return findings

        if state and state != info.state:
            findings.append(
                self._finding(
                    "zip-state",
                    join(path, "state"),
                    f"ZIP is in {info.state}",
                    f"{zip5} belongs to {info.state}; check the state mapping or a swapped field.",
                    payload_id,
                    signature=f"state:{info.state}",
                    preview_value=state,
                )
            )

        if city and info.cities:
            accepted = {normalize_city(name) for name in info.cities}
            normalized = normalize_city(city)
            if normalized not in accepted:
                suggestion = closest_match(normalized, accepted)
                hint = f"USPS preferred is {info.preferred_city_display!r}"
                if suggestion:
                    hint += f"; {suggestion!r} is also acceptable"
                findings.append(
                    self._finding(
                        "zip-city",
                        join(path, "city"),
                        "city is not associated with this ZIP",
                        hint + ".",
                        payload_id,
                        signature=f"zip:{zip5}",
                        preview_value=city,
                    )
                )
            elif info.preferred_city and normalized != normalize_city(info.preferred_city):
                findings.append(
                    self._finding(
                        "city-alias",
                        join(path, "city"),
                        "acceptable city variant",
                        f"USPS prefers {info.preferred_city_display!r}.",
                        payload_id,
                        severity=Severity.INFO,
                        preview_value=city,
                    )
                )

        if county and info.counties:
            known = {normalize_county(name) for name, _ in info.counties}
            if normalize_county(county) not in known:
                displayed = ", ".join(name for name, _ in info.counties)
                findings.append(
                    self._finding(
                        "zip-county",
                        join(path, "county"),
                        "county is not associated with this ZIP",
                        f"{zip5} is in {displayed}.",
                        payload_id,
                        signature=f"zip:{zip5}",
                        preview_value=county,
                    )
                )
        return findings

    # ------------------------------------------------------------------ batch

    def _employer(self, document: Any) -> tuple[str, ...] | None:
        if not self.config.employer_address:
            return None
        for _, value in resolve(document, self.config.employer_address):
            if isinstance(value, dict):
                return _address_key(value)
        return None

    def _employer_check(
        self,
        path: str,
        value: dict[str, Any],
        employer: tuple[str, ...] | None,
        config: GeoAddressConfig,
        payload_id: str,
    ) -> list[Finding]:
        if employer is None or not config.residential:
            return []
        if _address_key(value) != employer:
            return []
        return [
            self._finding(
                "home-equals-employer",
                path,
                "home address equals the employer address",
                "the office address may have been mapped into the home field.",
                payload_id,
                severity=Severity.WARNING,
            )
        ]

    def _remember_household(
        self,
        document: Any,
        path: str,
        value: dict[str, Any],
        config: GeoAddressConfig,
        payload_id: str,
    ) -> None:
        key_expr = config.household_key
        if not key_expr:
            return
        for _, key_value in resolve(document, key_expr):
            if not isinstance(key_value, str | int):
                continue
            self._households[str(key_value)].append(
                (payload_id, path, "|".join(_address_key(value)))
            )

    def finalize(self) -> list[Finding]:
        findings: list[Finding] = []
        findings.extend(self._defaulted_state_findings())
        findings.extend(self._household_findings())
        return findings

    def _defaulted_state_findings(self) -> list[Finding]:
        if len(self._defaulted) < 5:
            return []
        counts = Counter(state for _, _, state in self._defaulted)
        state, count = counts.most_common(1)[0]
        share = count / len(self._defaulted)
        if share < 0.9:
            return []
        return [
            self._finding(
                "defaulted-state",
                path,
                f"{share:.0%} of ZIP-state mismatches use {state}",
                "that usually means the state is hard-coded or defaulted upstream, "
                "not a per-record mistake.",
                payload_id,
                severity=Severity.WARNING,
                signature=f"state:{state}",
                preview_value=state,
            )
            for payload_id, path, observed in self._defaulted
            if observed == state
        ]

    def _household_findings(self) -> list[Finding]:
        findings: list[Finding] = []
        for members in self._households.values():
            addresses = {address for _, _, address in members}
            if len(addresses) < 2:
                continue
            for payload_id, path, _ in members:
                findings.append(
                    self._finding(
                        "household-address",
                        path,
                        "household members have different addresses",
                        "if they live together, one address was probably copied incompletely.",
                        payload_id,
                        severity=Severity.WARNING,
                    )
                )
        return findings

    # ------------------------------------------------------------------ helpers

    def _finding(
        self,
        rule: str,
        path: str,
        message: str,
        hint: str,
        payload_id: str,
        *,
        severity: Severity = Severity.ERROR,
        signature: str = "",
        preview_value: str | None = None,
    ) -> Finding:
        return Finding(
            layer=Layer.GEO,
            rule=rule,
            severity=severity,
            path=path,
            message=message,
            signature=signature,
            hint=hint,
            value_preview=preview_value,
            payload_id=payload_id,
        )


def _fields(value: dict[str, Any]) -> dict[str, Any]:
    return {str(key).lower(): item for key, item in value.items()}


def _text(fields: dict[str, Any], key: str) -> str:
    value = fields.get(key)
    return "" if value is None else str(value).strip()


def _address_key(value: dict[str, Any]) -> tuple[str, ...]:
    fields = _fields(value)
    zip_code = _text(fields, "zip")[:5]
    return (
        " ".join(_text(fields, "line1").upper().split()),
        normalize_city(_text(fields, "city")),
        _text(fields, "state").upper(),
        zip_code,
    )


def _line1_checks(
    path: str,
    line1: str,
    line2: str,
    config: GeoAddressConfig,
    payload_id: str,
) -> list[Finding]:
    findings: list[Finding] = []
    if config.residential and _PO_BOX.search(line1):
        findings.append(
            Finding(
                layer=Layer.GEO,
                rule="po-box",
                severity=Severity.ERROR,
                path=join(path, "line1"),
                message="PO box in a residential field",
                hint="a home address must be a physical residence.",
                value_preview=preview(line1),
                payload_id=payload_id,
            )
        )

    if config.line1_max is not None and len(line1) > config.line1_max:
        findings.append(
            Finding(
                layer=Layer.GEO,
                rule="line1-too-long",
                severity=Severity.WARNING,
                path=join(path, "line1"),
                message=f"longer than {config.line1_max} characters",
                hint="the destination limits this line; the value will be truncated or rejected.",
                value_preview=preview(line1),
                payload_id=payload_id,
            )
        )

    parts = _parse_address(line1)
    is_po_box = bool(_PO_BOX.search(line1))
    if parts is not None and "AddressNumber" not in parts and not is_po_box:
        findings.append(
            Finding(
                layer=Layer.GEO,
                rule="missing-street-number",
                severity=Severity.ERROR,
                path=join(path, "line1"),
                message="address line has no street number",
                hint="a deliverable US address starts with a primary number, e.g. 123 Main St.",
                value_preview=preview(line1),
                payload_id=payload_id,
            )
        )

    if _UNIT.search(line1) and not line2 and not is_po_box:
        findings.append(
            Finding(
                layer=Layer.GEO,
                rule="unit-in-line1",
                severity=Severity.WARNING,
                path=join(path, "line1"),
                message="unit appears in line 1",
                hint="move Apt/Ste/Unit into line 2 for destinations with separate unit fields.",
                value_preview=preview(line1),
                payload_id=payload_id,
            )
        )

    if _STATE_IN_LINE.search(line1):
        findings.append(
            Finding(
                layer=Layer.GEO,
                rule="state-in-line1",
                severity=Severity.WARNING,
                path=join(path, "line1"),
                message="state or ZIP appears in line 1",
                hint="keep line 1 to the street address; put city, state and ZIP in their fields.",
                value_preview=preview(line1),
                payload_id=payload_id,
            )
        )
    return findings


_STATE_IN_LINE = re.compile(r",\s*[A-Z]{2}\s+\d{5}(?:-\d{4})?\s*$")


def _parse_address(line1: str) -> dict[str, str] | None:
    try:
        tagged, _ = usaddress.tag(line1)
    except (usaddress.RepeatedLabelError, ValueError, TypeError):
        return None
    return cast("dict[str, str]", tagged)
