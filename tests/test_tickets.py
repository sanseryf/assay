"""Ticket building, redaction and the Jira client."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from assay.jira import JiraClient, JiraConfig, JiraError
from assay.loader import Payload
from assay.models import Group
from assay.pipeline import Pipeline
from assay.rules import RulesConfig
from assay.tickets import (
    build_ticket,
    expected_for,
    parse_path,
    render,
    render_adf,
    ticket_label,
)

SCHEMA = {
    "type": "object",
    "required": ["members"],
    "properties": {
        "members": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["zip"],
                "properties": {
                    "zip": {"type": "string", "pattern": "^[0-9]{5}$"},
                },
            },
        }
    },
}
PAYLOAD = {"members": [{"zip": 7030}]}


def check(payload=PAYLOAD) -> tuple[Group, list[Payload]]:
    payloads = [Payload("payload-1", json.dumps(payload).encode("utf-8"))]
    report = Pipeline(schema=SCHEMA, rules=RulesConfig(), redact=True).run(payloads)
    assert report.groups, "the fixture should produce a finding"
    return report.groups[0], payloads


def ticket():
    group, payloads = check()
    return build_ticket(group, payloads, SCHEMA)


def test_path_parser_handles_keys_indices_and_wildcards() -> None:
    assert [part.kind for part in parse_path("members[3].zip")] == ["key", "index", "key"]
    assert [part.kind for part in parse_path("$.members[*].zip")] == [
        "key",
        "wildcard",
        "key",
    ]


def test_expected_walks_properties_and_items() -> None:
    assert expected_for(SCHEMA, "members[0].zip") == "string, matching ^[0-9]{5}$"


def test_ticket_is_minimal_and_redacted() -> None:
    item = ticket()
    assert item.path == "members[0].zip"
    assert item.actual == "####"
    assert item.repro == {"members": [{"zip": "####"}]}
    assert item.label.startswith("assay-schema-")
    assert "7030" not in json.dumps(item.repro)
    assert item.expected == "string, matching ^[0-9]{5}$"


def test_ticket_label_is_jira_safe_and_stable() -> None:
    assert ticket_label("schema:type:members[*].zip:string") == (
        "assay-schema-type-members-zip-string"
    )
    assert ticket_label("syntax:bom:$") == "assay-syntax-bom"
    assert len(ticket_label("x" * 400)) <= 200


def test_renderers_agree_on_the_label_and_path() -> None:
    item = ticket()
    markdown = render(item, "markdown")
    jira = render(item, "jira")
    adf = render(item, "adf")
    assert item.label in markdown
    assert item.label in jira
    assert item.path in markdown
    assert item.path in jira
    assert json.loads(adf)["type"] == "doc"
    assert json.loads(render(item, "json"))["label"] == item.label


def test_adf_contains_a_code_block() -> None:
    blocks = [node["type"] for node in render_adf(ticket())["content"]]
    assert "codeBlock" in blocks
    assert "bulletList" in blocks


def config() -> JiraConfig:
    return JiraConfig(
        base_url="https://example.atlassian.net",
        email="me@example.com",
        token="secret-token",
        project="ABC",
    )


def test_jira_creates_an_issue_with_the_label() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"issues": []})
        return httpx.Response(201, json={"key": "ABC-7"})

    with JiraClient(config(), transport=httpx.MockTransport(handler)) as client:
        action, key = client.upsert(ticket())

    assert (action, key) == ("created", "ABC-7")
    search, create = requests
    credentials = base64.b64encode(b"me@example.com:secret-token").decode()
    assert search.headers["authorization"] == f"Basic {credentials}"
    body = json.loads(create.content)
    assert body["fields"]["labels"] == [ticket().label]
    assert body["fields"]["project"]["key"] == "ABC"
    assert body["fields"]["description"]["type"] == "doc"


def test_jira_comments_on_an_existing_issue() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={"issues": [{"key": "ABC-9"}]})
        return httpx.Response(201, json={})

    with JiraClient(config(), transport=httpx.MockTransport(handler)) as client:
        action, key = client.upsert(ticket())

    assert (action, key) == ("commented", "ABC-9")
    assert requests[-1].url.path.endswith("/issue/ABC-9/comment")
    assert "Assay saw this again" in requests[-1].content.decode()


def test_jira_errors_are_raised_without_leaking_the_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="nope secret-token")

    with (
        JiraClient(config(), transport=httpx.MockTransport(handler)) as client,
        pytest.raises(JiraError) as error,
    ):
        client.find_issue("assay-test")
    assert "HTTP 500" in str(error.value)
    assert "secret-token" not in str(error.value)
