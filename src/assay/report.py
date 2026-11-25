"""Rendering a report: the terminal table and the JSON that CI reads.

Rich renders ``Text`` objects here rather than markup strings, because payload
paths and messages are untrusted text: a field named ``[red]`` must print as
``[red]``, not recolour the report.
"""

from __future__ import annotations

import io
import json

from rich import box
from rich.console import Console, RenderableType
from rich.console import Group as RenderGroup
from rich.table import Table
from rich.text import Text

from .grouping import overlap_ratio
from .models import Group, Report, Severity

_LAYER_STYLE = {
    "syntax": "bold red",
    "schema": "bold yellow",
    "setup": "bold magenta",
    "string": "bold cyan",
    "geo": "bold blue",
}

_SEVERITY_STYLE = {
    Severity.ERROR: "red",
    Severity.WARNING: "yellow",
    Severity.INFO: "cyan",
}

_BAR_CELLS = 6


def summary_line(report: Report) -> Text:
    """The one-line header: what was scanned, what came back, how long it took."""
    text = Text()
    text.append("Assay", style="bold")
    if not report.payloads_scanned:
        text.append(" · no payloads scanned", style="yellow")
        return text

    text.append(f" · {report.payloads_scanned:,} payloads")
    text.append(f" · {report.payloads_clean:,} clean", style="green")
    if report.payloads_failed:
        label = "with errors" if report.errors else "with findings"
        text.append(f" · {report.payloads_failed:,} {label}", style=_bullet_style(report))
    text.append(f" · {report.duration_ms:,} ms", style="dim")
    return text


def _bullet_style(report: Report) -> str:
    if report.errors:
        return "red"
    if report.warnings:
        return "yellow"
    return "cyan"


def build_report(report: Report) -> RenderGroup:
    """The full terminal view: header, ranked groups, correlations, next step."""
    parts: list[RenderableType] = [summary_line(report)]
    if not report.groups:
        parts.append(Text("no problems found", style="green"))
        return RenderGroup(*parts)

    parts.append(Text(""))
    parts.append(_groups_table(report))

    notes = correlation_notes(report)
    if notes:
        parts.append(Text(""))
        parts.extend(notes)

    next_step = _next_step(report)
    if next_step is not None:
        parts.append(Text(""))
        parts.append(next_step)
    return RenderGroup(*parts)


def _groups_table(report: Report) -> Table:
    table = Table(
        box=box.SIMPLE_HEAD,
        show_edge=False,
        pad_edge=False,
        padding=(0, 1),
        expand=False,
        show_lines=False,
    )
    table.add_column("#", justify="right", style="dim")
    table.add_column("IMPACT", no_wrap=True)
    table.add_column("LAYER", no_wrap=True)
    table.add_column("WHERE", overflow="fold", max_width=40)
    table.add_column("PROBLEM", overflow="fold", ratio=3)
    table.add_column("RECORDS", justify="right")

    largest = report.groups[0].impact or 1.0
    for index, group in enumerate(report.groups, start=1):
        table.add_row(
            Text(str(index), style="dim"),
            _impact_bar(group, largest),
            Text(group.layer.value, style=_LAYER_STYLE.get(group.layer.value, "")),
            Text(_where(group), style="cyan", overflow="fold"),
            _problem(group),
            Text(f"{group.payloads_affected:,}"),
        )
    return table


def _impact_bar(group: Group, largest: float) -> Text:
    filled = max(1, round(_BAR_CELLS * group.impact / largest))
    return Text("█" * filled, style=_SEVERITY_STYLE.get(group.severity, ""))


def _where(group: Group) -> str:
    """Syntax findings have no JSON path, so show where the parser stopped."""
    if group.layer.value == "syntax" and group.findings:
        sample = group.findings[0]
        if sample.line is not None:
            return sample.location
    return group.path


def _problem(group: Group) -> Text:
    text = Text(group.message)
    if group.sample_values:
        text.append("\n")
        text.append("e.g. " + ", ".join(group.sample_values), style="dim")
    if group.hint:
        text.append("\n")
        text.append("hint: " + group.hint, style="dim italic")
    return text


def correlation_notes(report: Report) -> list[Text]:
    """Pairs of groups that always hit the same records."""
    by_fingerprint = {group.fingerprint: group for group in report.groups}
    seen: set[frozenset[str]] = set()
    notes: list[Text] = []
    for group in report.groups:
        for partner_fingerprint in group.correlated_with:
            pair = frozenset((group.fingerprint, partner_fingerprint))
            if pair in seen:
                continue
            seen.add(pair)
            partner = by_fingerprint.get(partner_fingerprint)
            if partner is None:
                continue
            share = overlap_ratio(group, partner)
            note = Text()
            note.append("correlated", style="bold magenta")
            note.append(f" · {group.path} and {partner.path} co-occur in ")
            note.append(f"{share:.0%}", style="bold")
            note.append(" of affected records; likely one upstream cause.")
            notes.append(note)
    return notes


def _next_step(report: Report) -> Text | None:
    """Name the single fix that clears the most findings."""
    if not report.groups or not report.findings_total:
        return None
    top = report.groups[0]
    share = top.count / report.findings_total
    if top.count == report.findings_total and len(report.groups) == 1:
        return Text(
            f"One cause: fixing {top.path} clears every finding in the batch.",
            style="bold",
        )
    text = Text()
    text.append("start here", style="bold")
    text.append(
        f" · fixing #{1} ({top.path}) clears {top.count:,} of {report.findings_total:,} "
        f"findings ({share:.0%})."
    )
    return text


def print_report(report: Report, *, console: Console | None = None) -> None:
    """Print the terminal view. Pass a console to redirect or disable colour."""
    (console or Console()).print(build_report(report))


def to_json(report: Report) -> str:
    """Stable JSON for CI: counts, groups and samples, no per-record noise."""
    return json.dumps(report.to_dict(), indent=2, ensure_ascii=False)


def to_text(report: Report, *, width: int = 120, ascii_only: bool = False) -> str:
    """Plain text rendering for a file or a pipe: no colour, no terminal detection.

    ``ascii_only`` is for environments that cannot encode the report's decoration:
    a redirected Windows console is cp1252, which has no block character or arrow.
    Payload content is left alone, because replacing it would hide the very thing
    the report is about.
    """
    buffer = io.StringIO()
    console = Console(
        file=buffer, width=width, force_terminal=False, no_color=True, highlight=False
    )
    console.print(build_report(report))
    text = buffer.getvalue().rstrip("\n")
    return _to_ascii(text) if ascii_only else text


_ASCII_GLYPHS = {
    "█": "#",
    "→": "->",
    "←": "<-",
    "↔": "<->",
    "✓": "ok",
    "✗": "x",
}


def _to_ascii(text: str) -> str:
    """Replace only glyphs cp1252 cannot encode, never payload content.

    Curly quotes, em dashes and middle dots are all in cp1252, and in this tool
    they are often the thing being reported, so they are left exactly as they are.
    """
    return "".join(_ASCII_GLYPHS.get(char, char) for char in text)
