"""HTTP interface: a web UI, the batch endpoint and the single-payload API.

``GET /`` serves the GUI. ``POST /assay/batch`` takes a list of payloads and returns the
same grouped report the CLI prints. ``POST /assay`` stays for single payloads and for
programmatic callers, as described in the architecture document.

The service is stateless: nothing is written to disk, payloads are not logged, and the
report is the only response.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import __version__
from .formats import to_html
from .geo.store import GeoDataError
from .geo.verify import VerificationError
from .loader import Payload
from .models import Report
from .pipeline import Limits, Pipeline
from .rules import ConfigError, RulesConfig, parse_config

PACKAGE_DIR = Path(__file__).parent
MAX_BATCH_PAYLOADS = 2_000
MAX_BATCH_BYTES = 10 * 1024 * 1024


class AssayRequest(BaseModel):
    """One payload checked against one schema."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    payload: Any
    schema_: dict[str, Any] = Field(alias="schema")
    rules: dict[str, Any] | None = None
    rules_yaml: str | None = None
    redact: bool = False

    @field_validator("schema_")
    @classmethod
    def _schema_is_an_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("schema must not be empty")
        return value


class BatchRequest(BaseModel):
    """A batch of payloads from the GUI."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    payloads: list[Any]
    schema_: dict[str, Any] = Field(alias="schema")
    rules: dict[str, Any] | None = None
    rules_yaml: str | None = None
    redact: bool = False

    @field_validator("schema_")
    @classmethod
    def _schema_is_an_object(cls, value: dict[str, Any]) -> dict[str, Any]:
        if not value:
            raise ValueError("schema must not be empty")
        return value


def create_app() -> FastAPI:
    templates = Jinja2Templates(directory=str(PACKAGE_DIR / "templates"))
    app = FastAPI(
        title="Assay",
        version=__version__,
        summary="Check JSON payloads and group the errors by root cause.",
    )
    app.mount("/static", StaticFiles(directory=str(PACKAGE_DIR / "static")), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index(request: Request) -> HTMLResponse:
        return templates.TemplateResponse(request, "index.html", {"version": __version__})

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.post("/assay")
    def assay(request: AssayRequest) -> dict[str, Any]:
        rules = _rules(request.rules, request.rules_yaml)
        raw = json.dumps(request.payload, ensure_ascii=False).encode("utf-8")
        pipeline = _pipeline(request.schema_, rules, request.redact)
        return _run(pipeline, [Payload(payload_id="request", raw=raw)])

    @app.post("/assay/batch")
    def assay_batch(request: BatchRequest) -> dict[str, Any]:
        report = _batch(request)
        return report.to_dict(include_occurrences=True)

    @app.post("/assay/export/html")
    def assay_export_html(request: BatchRequest) -> Response:
        report = _batch(request)
        return Response(
            content=to_html(report),
            media_type="text/html; charset=utf-8",
            headers={"Content-Disposition": 'attachment; filename="assay-report.html"'},
        )

    return app


def _batch(request: BatchRequest) -> Report:
    if not request.payloads:
        raise HTTPException(status_code=400, detail="send at least one payload")
    if len(request.payloads) > MAX_BATCH_PAYLOADS:
        raise HTTPException(
            status_code=413,
            detail=f"at most {MAX_BATCH_PAYLOADS:,} payloads per request",
        )
    rules = _rules(request.rules, request.rules_yaml)
    payloads = [
        Payload(
            payload_id=f"payload-{index}",
            raw=json.dumps(item, ensure_ascii=False).encode("utf-8"),
        )
        for index, item in enumerate(request.payloads, 1)
    ]
    total = sum(len(payload.raw) for payload in payloads)
    if total > MAX_BATCH_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"batch is larger than {MAX_BATCH_BYTES // (1024 * 1024)} MB",
        )
    pipeline = _pipeline(request.schema_, rules, request.redact)
    return _report(pipeline, payloads)


def _pipeline(schema: dict[str, Any], rules: RulesConfig, redact: bool) -> Pipeline:
    try:
        return Pipeline(
            schema=schema,
            rules=rules,
            redact=redact,
            limits=Limits(max_bytes=MAX_BATCH_BYTES),
        )
    except GeoDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except VerificationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _run(pipeline: Pipeline, payloads: list[Payload]) -> dict[str, Any]:
    report = _report(pipeline, payloads)
    return report.to_dict(include_occurrences=True)


def _report(pipeline: Pipeline, payloads: list[Payload]) -> Report:
    try:
        return pipeline.run(payloads)
    except GeoDataError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except VerificationError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


def _rules(document: dict[str, Any] | None, yaml_text: str | None) -> RulesConfig:
    if document is not None and yaml_text:
        raise HTTPException(status_code=400, detail="send rules or rules_yaml, not both")
    if yaml_text:
        try:
            return parse_config(yaml_text, source="rules")
        except ConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
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
