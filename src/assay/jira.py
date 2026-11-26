"""Minimal Jira Cloud issue client for ``assay ticket --push``.

The token comes from the environment or a command-line option and is never stored or
printed. Deduplication uses the ticket label: the next run searches for the label and
comments on the existing issue instead of opening a duplicate.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from .tickets import Ticket, render_adf


class JiraError(Exception):
    """The Jira API refused a request or could not be reached."""


@dataclass(frozen=True, slots=True)
class JiraConfig:
    base_url: str
    email: str
    token: str
    project: str
    issue_type: str = "Bug"
    timeout: float = 20.0

    def __post_init__(self) -> None:
        if not self.base_url.startswith(("http://", "https://")):
            raise JiraError("Jira URL must start with http:// or https://")
        if not self.project.strip():
            raise JiraError("a Jira project key is required for --push")


class JiraClient:
    def __init__(
        self,
        config: JiraConfig,
        *,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self.config = config
        self._client = httpx.Client(
            base_url=config.base_url.rstrip("/"),
            auth=(config.email, config.token),
            timeout=config.timeout,
            transport=transport,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> JiraClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> httpx.Response:
        try:
            response = self._client.request(method, path, **kwargs)
        except httpx.HTTPError as exc:
            raise JiraError(f"Jira request failed: {exc}") from exc
        if response.status_code >= 400:
            detail = response.text.replace(self.config.token, "***")[:400]
            raise JiraError(
                f"Jira returned HTTP {response.status_code}" + (f": {detail}" if detail else "")
            )
        return response

    def find_issue(self, label: str) -> str | None:
        """Find an existing issue by the Assay label, newest API first."""
        params: dict[str, str | int] = {
            "jql": f'labels = "{label}"',
            "fields": "key",
            "maxResults": 1,
        }
        for path in ("/rest/api/3/search/jql", "/rest/api/3/search"):
            try:
                response = self._client.request("GET", path, params=params)
            except httpx.HTTPError as exc:
                raise JiraError(f"Jira search failed: {exc}") from exc
            if response.status_code in (404, 410):
                continue
            if response.status_code >= 400:
                detail = response.text.replace(self.config.token, "***")[:300]
                raise JiraError(f"Jira search failed: HTTP {response.status_code}: {detail}")
            try:
                issues = response.json().get("issues", [])
            except ValueError as exc:
                raise JiraError("Jira search returned non-JSON") from exc
            if issues:
                return str(issues[0].get("key"))
            return None
        raise JiraError("Jira search API not found on this instance")

    def create(self, ticket: Ticket) -> str:
        fields = {
            "project": {"key": self.config.project},
            "issuetype": {"name": self.config.issue_type},
            "summary": ticket.summary,
            "description": render_adf(ticket),
            "labels": [ticket.label],
        }
        response = self._request("POST", "/rest/api/3/issue", json={"fields": fields})
        key = response.json().get("key")
        if not key:
            raise JiraError("Jira created no issue key")
        return str(key)

    def comment(self, issue_key: str, ticket: Ticket) -> None:
        body = {
            "body": {
                "type": "doc",
                "version": 1,
                "content": [
                    {
                        "type": "paragraph",
                        "content": [
                            {
                                "type": "text",
                                "text": "Assay saw this again: ",
                                "marks": [{"type": "strong"}],
                            },
                            {
                                "type": "text",
                                "text": (
                                    f"{ticket.affected} payload(s), {ticket.count} finding(s). "
                                    f"Actual: {ticket.actual}. "
                                    f"Fingerprint: {ticket.fingerprint}"
                                ),
                            },
                        ],
                    }
                ],
            }
        }
        self._request("POST", f"/rest/api/3/issue/{issue_key}/comment", json=body)

    def upsert(self, ticket: Ticket) -> tuple[str, str]:
        """Return ``("created" | "commented", issue_key)``."""
        existing = self.find_issue(ticket.label)
        if existing:
            self.comment(existing, ticket)
            return "commented", existing
        return "created", self.create(ticket)
