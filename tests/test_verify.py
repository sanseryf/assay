"""USPS verification: a client that is cached, throttled and never required."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from assay.geo.checks import GeoLayer
from assay.geo.store import ZipStore
from assay.geo.verify import (
    UspsVerifier,
    VerificationError,
    VerifiedAddress,
    cache_key,
)
from assay.models import Severity
from assay.rules import RulesConfig

CORRECTED = {
    "address": {
        "streetAddress": "123 MAIN ST",
        "secondaryAddress": "APT 4",
        "city": "MCKINNEY",
        "state": "TX",
        "ZIPCode": "75071",
        "ZIPPlus4": "1234",
    },
    "additionalInfo": {"DPVConfirmation": "Y"},
}
NO_MATCH = {"error": {"code": "404", "message": "Address not found"}}


class _Response:
    def __init__(self, payload: dict[str, object]) -> None:
        self._body = json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._body

    def __enter__(self) -> _Response:
        return self

    def __exit__(self, *_: object) -> bool:
        return False


class FakeTransport:
    def __init__(self, address_payload: dict[str, object]) -> None:
        self.address_payload = address_payload
        self.urls: list[str] = []

    def __call__(self, req, timeout: float):
        self.urls.append(req.full_url)
        if "oauth2" in req.full_url:
            return _Response({"access_token": "token-1"})
        return _Response(self.address_payload)

    @property
    def address_calls(self) -> int:
        return sum(1 for url in self.urls if "addresses" in url)


def make_verifier(tmp_path: Path, transport: FakeTransport, **overrides: object) -> UspsVerifier:
    options: dict[str, object] = {
        "cache_path": tmp_path / "usps.sqlite",
        "transport": transport,
        "max_per_minute": 600,
        "clock": lambda: 1_000.0,
        "sleep": lambda seconds: None,
    }
    options.update(overrides)
    return UspsVerifier("client", "secret", **options)  # type: ignore[arg-type]


def test_verification_returns_the_standardized_address(tmp_path: Path) -> None:
    transport = FakeTransport(CORRECTED)
    with make_verifier(tmp_path, transport) as verifier:
        result = verifier.verify(
            {
                "line1": "123 Main Street",
                "line2": "",
                "city": "McKinney",
                "state": "TX",
                "zip": "75071",
            }
        )
    assert result is not None
    assert result.line1 == "123 MAIN ST"
    assert result.zip4 == "1234"
    assert result.deliverable
    assert transport.address_calls == 1


def test_results_are_cached_by_normalized_address(tmp_path: Path) -> None:
    transport = FakeTransport(CORRECTED)
    with make_verifier(tmp_path, transport) as verifier:
        first = verifier.verify(
            {"line1": "123 main st", "city": "mckinney", "state": "tx", "zip": "75071"}
        )
        second = verifier.verify(
            {"line1": "123 MAIN ST", "city": "MCKINNEY", "state": "TX", "zip": "75071"}
        )
    assert first == second
    assert transport.address_calls == 1


def test_no_match_is_cached_too(tmp_path: Path) -> None:
    transport = FakeTransport(NO_MATCH)
    with make_verifier(tmp_path, transport) as verifier:
        assert (
            verifier.verify({"line1": "1 Nowhere", "city": "X", "state": "TX", "zip": "75071"})
            is None
        )
        assert (
            verifier.verify({"line1": "1 Nowhere", "city": "X", "state": "TX", "zip": "75071"})
            is None
        )
    assert transport.address_calls == 1


def test_cache_expires(tmp_path: Path) -> None:
    transport = FakeTransport(CORRECTED)
    now = [1_000.0]
    with make_verifier(tmp_path, transport, clock=lambda: now[0], ttl_days=30) as verifier:
        verifier.verify({"line1": "1 A", "city": "B", "state": "TX", "zip": "75071"})
        now[0] += 31 * 86_400
        verifier.verify({"line1": "1 A", "city": "B", "state": "TX", "zip": "75071"})
    assert transport.address_calls == 2


def test_missing_credentials_is_a_clear_error() -> None:
    with pytest.raises(VerificationError, match="USPS_CLIENT_ID"):
        UspsVerifier("", "")


def test_transport_failure_is_a_clear_error(tmp_path: Path) -> None:
    def broken(req, timeout):
        raise OSError("network down")

    with (
        make_verifier(tmp_path, broken) as verifier,
        pytest.raises(  # type: ignore[arg-type]
            VerificationError, match="token request failed"
        ),
    ):
        verifier.verify({"line1": "1 A", "city": "B", "state": "TX", "zip": "75071"})


def test_cache_key_ignores_case_and_spacing() -> None:
    assert cache_key(
        {"line1": " 123  main st ", "city": "mckinney", "state": "tx", "zip": "75071"}
    ) == cache_key({"line1": "123 MAIN ST", "city": "MCKINNEY", "state": "TX", "zip": "75071"})


class FakeVerifier:
    def __init__(self, result: VerifiedAddress | None, *, error: bool = False) -> None:
        self.result = result
        self.error = error

    def verify(self, address: dict[str, str]) -> VerifiedAddress | None:
        if self.error:
            raise VerificationError("USPS request failed: boom")
        return self.result


def geo_layer(store: ZipStore, verifier: FakeVerifier) -> GeoLayer:
    config = RulesConfig.model_validate(
        {"geo": {"addresses": [{"path": "$.members[*].home_address"}]}}
    ).geo
    return GeoLayer(config, store, verifier)


def document(**address: str) -> dict[str, object]:
    base = {"line1": "123 Main St", "city": "MCKINNEY", "state": "TX", "zip": "75071"}
    base.update(address)
    return {"members": [{"home_address": base}]}


def test_standardized_address_is_reported(geo_store: ZipStore) -> None:
    layer = geo_layer(
        geo_store,
        FakeVerifier(
            VerifiedAddress(
                line1="123 MAIN STREET",
                line2="",
                city="MCKINNEY",
                state="TX",
                zip="75071",
                zip4="1234",
            )
        ),
    )
    findings = layer.check(document(), "p1")
    assert findings[0].rule == "address-corrected"
    assert "USPS:" in (findings[0].hint or "")


def test_undeliverable_address_is_an_error(geo_store: ZipStore) -> None:
    layer = geo_layer(
        geo_store,
        FakeVerifier(
            VerifiedAddress(
                line1="123 MAIN ST",
                line2="",
                city="MCKINNEY",
                state="TX",
                zip="75071",
                deliverable=False,
            )
        ),
    )
    findings = layer.check(document(), "p1")
    assert findings[0].rule == "address-not-deliverable"
    assert findings[0].severity is Severity.ERROR


def test_no_match_is_reported(geo_store: ZipStore) -> None:
    findings = geo_layer(geo_store, FakeVerifier(None)).check(document(), "p1")
    assert findings[0].rule == "address-unverified"


def test_verification_failure_is_a_warning_not_a_crash(geo_store: ZipStore) -> None:
    findings = geo_layer(geo_store, FakeVerifier(None, error=True)).check(document(), "p1")
    assert findings[0].rule == "verify-failed"
    assert findings[0].severity is Severity.WARNING


def test_cli_verify_without_credentials_exits_three(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    from assay.cli import run

    monkeypatch.delenv("USPS_CLIENT_ID", raising=False)
    monkeypatch.delenv("USPS_CLIENT_SECRET", raising=False)
    schema = tmp_path / "schema.json"
    schema.write_text(json.dumps({"type": "object"}), encoding="utf-8")
    rules = tmp_path / "rules.yaml"
    rules.write_text(
        'geo:\n  addresses:\n    - path: "$.members[*].home_address"\n',
        encoding="utf-8",
    )
    payload = tmp_path / "payload.json"
    payload.write_text(
        '{"members": [{"home_address": {"line1": "123 Main St", "city": "MCKINNEY", '
        '"state": "TX", "zip": "75071"}}]}',
        encoding="utf-8",
    )
    code = run(
        [
            "check",
            str(payload),
            "--schema",
            str(schema),
            "--rules",
            str(rules),
            "--verify",
            "--quiet",
        ]
    )
    assert code == 3
    assert "USPS_CLIENT_ID" in capsys.readouterr().err
