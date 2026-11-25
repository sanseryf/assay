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
from .loader import InputError, read_payloads
from .pipeline import DEFAULT_MAX_BYTES, DEFAULT_MAX_DEPTH, Limits, Pipeline
from .report import print_report, to_json, to_text

app = typer.Typer(
    add_completion=False,
    no_args_is_help=True,
    help="Check JSON payloads and group the errors by root cause.",
)

FORMATS = ("terminal", "json")


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
    output_format: str = typer.Option(
        "terminal",
        "--format",
        "-f",
        help="Report format: terminal or json.",
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
    try:
        pipeline = Pipeline(
            schema=schema_document,
            schema_id=str(schema),
            limits=Limits(max_bytes=max_bytes, max_depth=max_depth),
        )
    except SchemaError as exc:
        _fail(f"invalid schema {schema}: {exc.message}")

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

    rendered = to_json(report) if report_format == "json" else to_text(report)
    if output is not None:
        try:
            output.write_text(rendered + "\n", encoding="utf-8")
        except OSError as exc:
            _fail(f"cannot write {output}: {exc.strerror or exc}")
    elif report_format == "json":
        _emit(rendered)
    elif _stdout_is_a_terminal():
        print_report(report)
    else:
        # A pipe or a redirected file: plain ASCII survives a cp1252 console and
        # keeps CI logs readable. `--output` still writes the Unicode version.
        _emit(to_text(report, ascii_only=True))

    raise _Status(report.exit_code)


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
