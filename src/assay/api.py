"""HTTP interface: one endpoint and a paste-in page.

``POST /assay`` takes a payload, a schema and optional rules and returns the same
grouped report the CLI prints. ``GET /`` serves a small page that posts to it, so the
tool can be used without a terminal.

The service is stateless: nothing is written to disk, payloads are not logged, and the
report is the only response. That matches the CLI, and it keeps the privacy story
simple.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import __version__
from .geo.store import GeoDataError
from .geo.verify import VerificationError
from .loader import Payload
from .pipeline import Pipeline
from .rules import RulesConfig


class AssayRequest(BaseModel):
    """One payload checked against one schema."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    payload: Any
    schema_: dict[str, Any] = Field(alias="schema")
    rules: dict[str, Any] | None = None
    redact: bool = False

    @field_validator("schema_")
    @classmethod
    def _schema_is_an_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("schema must not be empty")
        return value


def create_app() -> FastAPI:
    app = FastAPI(
        title="Assay",
        version=__version__,
        summary="Check JSON payloads and group the errors by root cause.",
    )

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return _PAGE

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/assay")
    def assay(request: AssayRequest) -> dict[str, Any]:
        rules = _rules(request.rules)
        raw = json.dumps(request.payload, ensure_ascii=False).encode("utf-8")
        try:
            pipeline = Pipeline(schema=request.schema_, rules=rules, redact=request.redact)
            report = pipeline.run([Payload(payload_id="request", raw=raw)])
        except GeoDataError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except VerificationError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return report.to_dict()

    return app


def _rules(document: dict[str, Any] | None) -> RulesConfig:
    if document is None:
        return RulesConfig()
    try:
        return RulesConfig.model_validate(document)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise HTTPException(status_code=400, detail=f"invalid rules: {details}") from exc


app = create_app()


_PAGE = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Assay</title>
<style>
:root { color-scheme: light dark; }
body { font: 15px/1.5 system-ui, sans-serif; margin: 2rem auto; max-width: 70rem; padding: 0 1rem; }
h1 { font-size: 1.4rem; margin-bottom: .25rem; }
p.lede { color: #666; margin-top: 0; }
label { display: block; font-weight: 600; margin: 1rem 0 .25rem; }
textarea { width: 100%; font: 13px/1.4 ui-monospace, monospace; box-sizing: border-box; }
#payload { height: 10rem; } #schema { height: 8rem; } #rules { height: 6rem; }
button { margin-top: 1rem; padding: .5rem 1.2rem; font: inherit; cursor: pointer; }
#out { margin-top: 1.5rem; }
.group { border: 1px solid #ccc; border-radius: 8px; padding: .8rem 1rem; margin: .75rem 0; }
.group h2 { font-size: .95rem; margin: 0; font-family: ui-monospace, monospace; }
.layer { color: #666; font-size: .8rem; text-transform: uppercase; letter-spacing: .05em; }
.hint { color: #555; }
pre { white-space: pre-wrap; }
</style>
</head>
<body>
<h1>Assay</h1>
<p class="lede">Paste a JSON payload, a JSON Schema, and optionally a rules object.</p>
<label for="payload">Payload</label>
<textarea id="payload">{"members": [{"zip": 7030}]}</textarea>
<label for="schema">Schema</label>
<textarea id="schema">{"type": "object", "properties": {"members": {"type": "array", "items": {"type": "object", "properties": {"zip": {"type": "string"}}}}}}</textarea>
<label for="rules">Rules (optional)</label>
<textarea id="rules"></textarea>
<button id="run">Check</button>
<div id="out"></div>
<script>
const esc = (s) => String(s).replace(/[&<>]/g, (c) => ({"&": "&amp;", "<": "&lt;", ">": "&gt;"}[c]));
document.getElementById("run").addEventListener("click", async () => {
  const out = document.getElementById("out");
  let body;
  try {
    body = {
      payload: JSON.parse(document.getElementById("payload").value),
      schema: JSON.parse(document.getElementById("schema").value),
    };
  } catch (err) { out.innerHTML = "<p>Invalid JSON: " + esc(err.message) + "</p>"; return; }
  const rules = document.getElementById("rules").value.trim();
  if (rules) {
    try { body.rules = JSON.parse(rules); }
    catch (err) { out.innerHTML = "<p>Invalid rules JSON: " + esc(err.message) + "</p>"; return; }
  }
  out.innerHTML = "<p>Checking...</p>";
  const res = await fetch("/assay", {
    method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(body),
  });
  if (!res.ok) { out.innerHTML = "<p>Error " + res.status + ": " + esc((await res.json()).detail || "") + "</p>"; return; }
  const report = await res.json();
  let html = "<p>" + report.payloads_scanned + " payloads, " + report.payloads_clean + " clean, "
    + report.findings_total + " findings in " + report.groups.length + " groups</p>";
  for (const g of report.groups) {
    html += "<section class='group'><h2>" + esc(g.path) + "</h2><p class='layer'>"
      + esc(g.layer) + " &middot; " + esc(g.severity) + " &middot; " + g.payloads_affected
      + " records</p><p>" + esc(g.message) + "</p>"
      + (g.sample_values.length ? "<p>e.g. " + esc(g.sample_values.join(", ")) + "</p>" : "")
      + (g.hint ? "<p class='hint'>" + esc(g.hint) + "</p>" : "") + "</section>";
  }
  out.innerHTML = html;
});
</script>
</body>
</html>
"""
