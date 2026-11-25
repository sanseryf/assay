"""The HTTP interface: one endpoint and a paste-in page."""

from __future__ import annotations

import pytest

pytest.importorskip("fastapi")

from fastapi.testclient import TestClient

from assay.api import create_app

SCHEMA = {
    "type": "object",
    "properties": {
        "members": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"zip": {"type": "string"}},
            },
        }
    },
}

BAD = {"members": [{"zip": 7030}]}
GOOD = {"members": [{"zip": "75071"}]}


@pytest.fixture()
def client() -> TestClient:
    return TestClient(create_app())


def test_health(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_index_serves_the_paste_in_page(client: TestClient) -> None:
    response = client.get("/")
    assert response.status_code == 200
    assert "textarea" in response.text
    assert "Assay" in response.text


def test_clean_payload(client: TestClient) -> None:
    response = client.post("/assay", json={"payload": GOOD, "schema": SCHEMA})
    assert response.status_code == 200
    report = response.json()
    assert report["payloads_scanned"] == 1
    assert report["payloads_clean"] == 1
    assert report["groups"] == []


def test_errors_are_grouped(client: TestClient) -> None:
    response = client.post("/assay", json={"payload": BAD, "schema": SCHEMA})
    assert response.status_code == 200
    report = response.json()
    assert report["groups"][0]["path"] == "members[*].zip"
    assert report["errors"] == 1


def test_rules_are_applied(client: TestClient) -> None:
    rules = {
        "rules": [
            {
                "id": "dates",
                "kind": "compare",
                "left": "$.start",
                "op": "<=",
                "right": "$.end",
            }
        ]
    }
    response = client.post(
        "/assay",
        json={
            "payload": {"start": "2024-09-01", "end": "2024-01-01"},
            "schema": {"type": "object"},
            "rules": rules,
        },
    )
    assert response.status_code == 200
    assert response.json()["groups"][0]["layer"] == "setup"


def test_invalid_rules_are_a_400(client: TestClient) -> None:
    response = client.post(
        "/assay", json={"payload": GOOD, "schema": SCHEMA, "rules": {"nope": True}}
    )
    assert response.status_code == 400
    assert "invalid rules" in response.json()["detail"]


def test_missing_schema_is_a_422(client: TestClient) -> None:
    response = client.post("/assay", json={"payload": GOOD})
    assert response.status_code == 422


def test_redaction_applies_to_the_api(client: TestClient) -> None:
    response = client.post("/assay", json={"payload": BAD, "schema": SCHEMA, "redact": True})
    report = response.json()
    assert report["redacted"] is True
    assert report["groups"][0]["sample_values"] == ["####"]
