"""Command line entry point.

Exit codes are part of the interface, because this tool is meant to run in CI:

======  ===============================================
code    meaning
======  ===============================================
0       no errors or warnings (informational notes may still be listed)
1       findings, all of them warnings
2       at least one error found
3       Assay could not run at all: bad schema, unreadable input, bad command line
======  ===============================================

A schema that fails to compile is exit 3, not 2: nothing was checked, and reporting
a verdict would be a lie. For the same reason a typo on the command line must not
look like "errors found", which is why this module drives the Typer app through
:func:`run` instead of letting the framework exit for us.
"""

from __future__ import annotations

import json
import sys
import traceback
from collections.abc import Sequence
from pathlib import Path
from typing import NoReturn

import typer
from jsonschema.exceptions import SchemaError
from rich.console import Console
from typer.exceptions import TyperException

from . import __version__
from .formats import to_html, to_sarif
from .geo.store import GeoDataError
from .geo.verify import VerificationError
from .loader import InputError, read_payloads
from .models import Group, Report
from .pipeline import DEFAULT_MAX_BYTES, DEFAULT_MAX_DEPTH, Limits, Pipeline
from .report import print_report, to_json, to_text
from .rules import ConfigError, RulesConfig, load_config
from .tickets import build_ticket, render

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Check JSON payloads and group the errors by root cause.",
)

FORMATS = ("terminal", "json", "html", "sarif")
TICKET_FORMATS = ("markdown", "jira", "adf", "json")


class _Status(Exception):
    """Carry the process exit code past Typer, which swallows ``typer.Exit``.

    Typer's own ``Exit`` is converted into a normal return when the app is invoked
    with ``standalone_mode=False``, so the exit code is carried in a plain
    exception that nothing in the framework intercepts.
    """

    def __init__(self, code: int) -> None:
        super().__init__(code)
        self.code = code


def _fail(message: str) -> NoReturn:
    """Assay itself failed: report it and exit 3, never 2."""
    typer.secho(f"assay: {message}", fg="red", err=True)
    raise _Status(3)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"assay {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    version: bool = typer.Option(
        False,
        "--version",
        callback=_version_callback,
        is_eager=True,
        help="Show the version and exit.",
    ),
) -> None:
    """Check JSON payloads and group the errors by root cause."""


@app.command()
def check(
    paths: list[str] = typer.Argument(
        ...,
        metavar="PATHS",
        help="JSON files, NDJSON batches, globs, directories, or - for stdin.",
    ),
    schema: Path = typer.Option(
        ...,
        "--schema",
        "-s",
        exists=True,
        dir_okay=False,
        readable=True,
        help="JSON Schema (Draft 2020-12) to validate against.",
    ),
    rules: Path | None = typer.Option(
        None,
        "--rules",
        "-r",
        exists=True,
        dir_okay=False,
        readable=True,
        help="YAML rules for setup checks, string limits and batch smells.",
    ),
    output_format: str = typer.Option(
        "terminal",
        "--format",
        "-f",
        help="Report format: terminal, json, html or sarif.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the report to a file instead of stdout.",
    ),
    quiet: bool = typer.Option(
        False,
        "--quiet",
        "-q",
        help="Print nothing; the exit code is the answer.",
    ),
    verify: bool = typer.Option(
        False,
        "--verify",
        help="Ask USPS to verify addresses (needs USPS_CLIENT_ID / USPS_CLIENT_SECRET).",
    ),
    redact: bool = typer.Option(
        False,
        "--redact",
        help="Replace values with shape tokens in the report.",
    ),
    max_bytes: int = typer.Option(
        DEFAULT_MAX_BYTES,
        "--max-bytes",
        min=1,
        help="Reject payloads larger than this many bytes.",
    ),
    max_depth: int = typer.Option(
        DEFAULT_MAX_DEPTH,
        "--max-depth",
        min=1,
        help="Reject payloads nested deeper than this.",
    ),
) -> None:
    """Check one payload, or a whole batch, against a schema."""
    report_format = output_format.lower().strip()
    if report_format not in FORMATS:
        _fail(f"unknown --format {output_format!r}; expected terminal or json")

    schema_document = _load_schema(schema)
    rules_config = RulesConfig()
    if rules is not None:
        try:
            rules_config = load_config(rules)
        except ConfigError as exc:
            _fail(str(exc))
    if verify and not rules_config.geo.verify.enabled:
        rules_config = rules_config.model_copy(
            update={
                "geo": rules_config.geo.model_copy(
                    update={"verify": rules_config.geo.verify.model_copy(update={"enabled": True})}
                )
            }
        )
    try:
        pipeline = Pipeline(
            schema=schema_document,
            schema_id=str(schema),
            rules=rules_config,
            limits=Limits(max_bytes=max_bytes, max_depth=max_depth),
            redact=redact,
        )
    except SchemaError as exc:
        _fail(f"invalid schema {schema}: {exc.message}")
    except GeoDataError as exc:
        _fail(str(exc))
    except VerificationError as exc:
        _fail(str(exc))

    try:
        payloads = read_payloads(paths, max_bytes=max_bytes)
        report = pipeline.run(payloads)
    except InputError as exc:
        _fail(str(exc))
    except OSError as exc:
        _fail(f"cannot read input: {exc.strerror or exc}")

    if report.payloads_scanned == 0:
        _fail("no payloads found in the given inputs")

    if quiet:
        raise _Status(report.exit_code)

    rendered = _render(report, report_format)
    if output is not None:
        try:
            output.write_text(rendered + "\n", encoding="utf-8")
        except OSError as exc:
            _fail(f"cannot write {output}: {exc.strerror or exc}")
    elif report_format == "terminal":
        if _stdout_is_a_terminal():
            print_report(report)
        else:
            # A pipe or a redirected file: plain ASCII survives a cp1252 console and
            # keeps CI logs readable. `--output` still writes the Unicode version.
            _emit(to_text(report, ascii_only=True))
    else:
        _emit(rendered)

    raise _Status(report.exit_code)


def _render(report: Report, report_format: str) -> str:
    if report_format == "json":
        return to_json(report)
    if report_format == "html":
        return to_html(report)
    if report_format == "sarif":
        return to_sarif(report)
    return to_text(report)


def _stdout_is_a_terminal() -> bool:
    try:
        return bool(Console().is_terminal)
    except Exception:  # pragma: no cover - a broken stdout is not worth failing over
        return False


def _emit(text: str) -> None:
    """Write text, falling back to UTF-8 bytes on a console that cannot encode it."""
    try:
        typer.echo(text)
    except UnicodeEncodeError:  # e.g. cp1252 stdout with a non-Latin sample value
        sys.stdout.buffer.write((text + "\n").encode("utf-8"))
        sys.stdout.flush()


@app.command()
def ticket(
    paths: list[str] = typer.Argument(
        ...,
        metavar="PATHS",
        help="JSON files, NDJSON batches, globs, directories, or - for stdin.",
    ),
    schema: Path = typer.Option(
        ...,
        "--schema",
        "-s",
        exists=True,
        dir_okay=False,
        readable=True,
        help="JSON Schema (Draft 2020-12) to validate against.",
    ),
    rules: Path | None = typer.Option(
        None,
        "--rules",
        "-r",
        exists=True,
        dir_okay=False,
        readable=True,
        help="YAML rules for setup checks, string limits and batch smells.",
    ),
    group: str = typer.Option(
        "1",
        "--group",
        "-g",
        help="1-based group number, or a fingerprint.",
    ),
    output_format: str = typer.Option(
        "markdown",
        "--format",
        "-f",
        help="Ticket format: markdown, jira, adf or json.",
    ),
    output: Path | None = typer.Option(
        None,
        "--output",
        "-o",
        help="Write the ticket to a file instead of stdout.",
    ),
    redact: bool = typer.Option(
        True,
        "--redact/--no-redact",
        help="Redact values in the ticket. On by default.",
    ),
    push: bool = typer.Option(
        False,
        "--push",
        help="Create the Jira issue, or comment on the one with this label.",
    ),
    jira_url: str | None = typer.Option(
        None,
        "--jira-url",
        envvar="JIRA_URL",
        help="Jira Cloud base URL, for example https://team.atlassian.net.",
    ),
    jira_email: str | None = typer.Option(
        None,
        "--jira-email",
        envvar="JIRA_EMAIL",
        help="Atlassian account email.",
    ),
    jira_token: str | None = typer.Option(
        None,
        "--jira-token",
        envvar="JIRA_API_TOKEN",
        help="API token. Prefer the JIRA_API_TOKEN environment variable.",
    ),
    project: str | None = typer.Option(
        None,
        "--project",
        envvar="JIRA_PROJECT",
        help="Jira project key.",
    ),
    issue_type: str = typer.Option(
        "Bug",
        "--issue-type",
        envvar="JIRA_ISSUE_TYPE",
        help="Jira issue type name.",
    ),
    max_bytes: int = typer.Option(
        DEFAULT_MAX_BYTES,
        "--max-bytes",
        help="Reject payloads larger than this many bytes.",
    ),
    max_depth: int = typer.Option(
        DEFAULT_MAX_DEPTH,
        "--max-depth",
        min=1,
        help="Reject payloads nested deeper than this.",
    ),
) -> None:
    """Build a minimal, redacted ticket for one report group.

    With ``--push`` the ticket is created in Jira Cloud, or added as a comment when an
    issue already carries the group's label. Credentials are read from the environment
    and never written to disk.
    """
    target_format = output_format.lower().strip()
    if target_format not in TICKET_FORMATS:
        _fail(f"unknown --format {output_format!r}; expected markdown, jira, adf or json")

    schema_document = _load_schema(schema)
    rules_config = RulesConfig()
    if rules is not None:
        try:
            rules_config = load_config(rules)
        except ConfigError as exc:
            _fail(str(exc))
    try:
        pipeline = Pipeline(
            schema=schema_document,
            schema_id=str(schema),
            rules=rules_config,
            limits=Limits(max_bytes=max_bytes, max_depth=max_depth),
            redact=True,
        )
    except SchemaError as exc:
        _fail(f"invalid schema {schema}: {exc.message}")
    except GeoDataError as exc:
        _fail(str(exc))
    except VerificationError as exc:
        _fail(str(exc))

    try:
        payloads = list(read_payloads(paths, max_bytes=max_bytes))
        report = pipeline.run(payloads)
    except InputError as exc:
        _fail(str(exc))
    except OSError as exc:
        _fail(f"cannot read input: {exc.strerror or exc}")

    if report.payloads_scanned == 0:
        _fail("no payloads found in the given inputs")
    if not report.groups:
        _fail("no findings, so there is no ticket to build")

    selected = _select_group(report, group)
    generated = build_ticket(selected, payloads, schema_document, redact=redact)
    rendered = render(generated, target_format)

    if output is not None:
        try:
            output.write_text(rendered + "\n", encoding="utf-8")
        except OSError as exc:
            _fail(f"cannot write {output}: {exc.strerror or exc}")
    elif not push:
        _emit(rendered)

    if not push:
        return

    if not (jira_url and jira_email and jira_token and project):
        _fail("--push needs JIRA_URL, JIRA_EMAIL, JIRA_API_TOKEN and JIRA_PROJECT")
    try:
        from .jira import JiraClient, JiraConfig, JiraError
    except ImportError:
        _fail('Jira support needs the optional dependency: pip install "assay[jira]"')
    try:
        with JiraClient(
            JiraConfig(
                base_url=jira_url,
                email=jira_email,
                token=jira_token,
                project=project,
                issue_type=issue_type,
            )
        ) as client:
            action, key = client.upsert(generated)
    except (JiraError, ValueError) as exc:
        _fail(str(exc))
    typer.echo(f"{action} {key}")


def _select_group(report: Report, selector: str) -> Group:
    if selector.isdigit():
        index = int(selector)
        if index < 1 or index > len(report.groups):
            _fail(f"group {index} is out of range; the report has {len(report.groups)}")
        return report.groups[index - 1]
    for candidate in report.groups:
        if candidate.fingerprint == selector:
            return candidate
    _fail(f"no group matches fingerprint {selector!r}")


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Interface to bind."),
    port: int = typer.Option(8000, help="Port to bind."),
) -> None:
    """Serve the HTTP API and the paste-in page."""
    try:
        import uvicorn

        from .api import create_app
    except ImportError:
        _fail('the API needs the optional dependencies: pip install "assay[api]"')
    uvicorn.run(create_app(), host=host, port=port)


def _load_schema(path: Path) -> dict[str, object]:
    """Read the schema file, failing with exit 3 rather than raising a traceback."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        _fail(f"cannot read schema {path}: {exc.strerror or exc}")
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        _fail(f"schema {path} is not valid JSON: {exc}")
    if not isinstance(document, dict):
        _fail(f"schema {path} must be a JSON object")
    return document


def run(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return the exit code instead of raising ``SystemExit``.

    This is the seam the tests use: usage errors become 3 here, where the exit-code
    contract can actually be enforced.
    """
    try:
        app(
            args=list(argv) if argv is not None else None,
            prog_name="assay",
            standalone_mode=False,
        )
    except _Status as status:
        return status.code
    except TyperException as exc:
        _show_usage_error(exc)
        return 3
    except typer.Exit as exc:  # --version and --help
        return int(getattr(exc, "exit_code", 0))
    return 0


def _show_usage_error(exc: TyperException) -> None:
    """Print a usage error the way the framework would, without exiting for us."""
    show = getattr(exc, "show", None)
    if callable(show):
        show()
    else:  # pragma: no cover - only for exotic Typer exceptions
        print(f"assay: {exc}", file=sys.stderr)


def main() -> None:
    """Console-script entry point."""
    try:
        code = run()
    except BrokenPipeError:  # e.g. `assay check ... | head`
        code = 0
    except Exception as exc:  # a bug in Assay, not in the payload
        traceback.print_exc()
        typer.secho(f"assay: internal error: {exc}", fg="red", err=True)
        code = 3
    raise SystemExit(code)


if __name__ == "__main__":  # pragma: no cover
    main()
