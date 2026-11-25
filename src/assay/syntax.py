"""Layer 1: is it even valid JSON?

``json.loads`` tells you where the parser gave up. That is a position, not a
diagnosis. These detectors look at the same text and explain the human cause: a
comma left behind an edit, quotes autocorrected by Word, a payload cut off
mid-write, a file that is not UTF-8.

Everything except BOM and encoding damage stops the pipeline for that payload,
because nothing downstream can run on text that does not parse.
"""

from __future__ import annotations

import json
import re
from bisect import bisect_right
from dataclasses import dataclass, field
from typing import Any

from .models import Finding, Layer, Severity

BOM = "\ufeff"

SMART_QUOTES: dict[str, str] = {
    "\u201c": "U+201C (left double quotation mark)",
    "\u201d": "U+201D (right double quotation mark)",
    "\u2018": "U+2018 (left single quotation mark)",
    "\u2019": "U+2019 (right single quotation mark)",
}

_TRAILING_COMMA = re.compile(r",\s*[}\]]")
_COMMENT = re.compile(r"//|/\*")
_SINGLE_QUOTE_KEY = re.compile(r"(?P<pre>[{:,\[])\s*'")
_NON_FINITE = re.compile(r"(?<![\w.])-?Infinity(?![\w])|(?<![\w.])NaN(?![\w])")


class _NonFinite(ValueError):
    """Raised by :func:`_reject_constant` so NaN/Infinity never parse as numbers."""

    def __init__(self, token: str) -> None:
        super().__init__(token)
        self.token = token


def _reject_constant(token: str) -> Any:
    """``json`` calls this for NaN/Infinity; raising is the only way to refuse them."""
    raise _NonFinite(token)


@dataclass(slots=True)
class SyntaxResult:
    """What the syntax layer could make of one payload.

    ``text`` is kept even when parsing fails, because the caller may want to show
    the offending line. ``document`` is only meaningful when ``ok`` is true: JSON
    ``null`` is a valid document, so ``None`` alone cannot signal failure.
    """

    findings: list[Finding] = field(default_factory=list)
    text: str | None = None
    document: Any = None
    ok: bool = False


@dataclass(slots=True)
class _Scan:
    """Facts gathered in one pass over the text."""

    spans: list[tuple[int, int]] = field(default_factory=list)
    unescaped_newline: int | None = None
    open_stack: list[tuple[str, int]] = field(default_factory=list)
    mismatched: tuple[str, int, str] | None = None
    unterminated_string: int | None = None


def analyze(raw: bytes, payload_id: str = "") -> SyntaxResult:
    """Decode, detect, and parse one payload."""
    text, encoding_finding = _decode(raw, payload_id)
    if encoding_finding is not None:
        return SyntaxResult(findings=[encoding_finding], text=None, ok=False)

    assert text is not None
    findings: list[Finding] = []

    if text.startswith(BOM):
        findings.append(
            _finding(
                "bom",
                "byte-order mark at start of file",
                payload_id,
                pos=0,
                text=text,
                hint="strip the BOM; some Windows editors save UTF-8 with one.",
            )
        )
        text = text[len(BOM) :]

    try:
        document = json.loads(text, parse_constant=_reject_constant)
    except _NonFinite as exc:
        match = _outside(_NON_FINITE, text, _scan(text).spans)
        pos = match.start() if match else None
        findings.append(
            _finding(
                "non-finite",
                f"{exc.token} is not valid JSON",
                payload_id,
                pos=pos,
                text=text,
                hint="use null, or a quoted string and convert it downstream.",
                preview=exc.token,
            )
        )
        return SyntaxResult(findings=findings, text=text, ok=False)
    except json.JSONDecodeError as exc:
        findings.extend(_diagnose(text, exc, payload_id))
        return SyntaxResult(findings=findings, text=text, ok=False)
    except RecursionError:
        findings.append(
            _finding(
                "input-too-deep",
                "nesting too deep to parse",
                payload_id,
                hint="flatten the payload; the parser, not Assay, hit its stack limit.",
            )
        )
        return SyntaxResult(findings=findings, text=text, ok=False)

    return SyntaxResult(findings=findings, text=text, document=document, ok=True)


def _decode(raw: bytes, payload_id: str) -> tuple[str | None, Finding | None]:
    """Decode UTF-8, or explain exactly which byte broke and what it probably was."""
    try:
        return raw.decode("utf-8"), None
    except UnicodeDecodeError as exc:
        latin = raw.decode("latin-1")
        line, col = _line_col(latin, exc.start)
        offending = raw[exc.start : exc.end]
        try:
            decoded = offending.decode("cp1252")
            description = f"0x{offending[0]:02x} is {decoded!r} in cp1252"
        except UnicodeDecodeError:
            description = f"0x{offending[0]:02x} is not valid cp1252 either"
        hint = (
            f"{description}; byte offset {exc.start}. Re-export the file as UTF-8 "
            "(cp1252 and latin-1 are the usual sources)."
        )
        finding = Finding(
            layer=Layer.SYNTAX,
            rule="encoding",
            severity=Severity.ERROR,
            path="$",
            message="not valid UTF-8",
            hint=hint,
            payload_id=payload_id,
            line=line,
            col=col,
        )
        return None, finding


def _diagnose(text: str, error: json.JSONDecodeError, payload_id: str) -> list[Finding]:
    """Run every detector, then report each cause it can prove, in text order."""
    scan = _scan(text)
    truncated = bool(scan.open_stack) or scan.unterminated_string is not None
    candidates: list[tuple[int, Finding]] = []

    def add(pos: int, rule: str, message: str, hint: str, preview: str | None = None) -> None:
        candidates.append(
            (
                pos,
                _finding(rule, message, payload_id, pos=pos, text=text, hint=hint, preview=preview),
            )
        )

    smart = _first_smart_quote(text, scan.spans)
    if smart is not None:
        pos, char = smart
        add(
            pos,
            "smart-quotes",
            f"curly quote {SMART_QUOTES[char]}",
            "replace “ ” ‘ ’ with straight quotes and re-export as UTF-8; "
            "Word, Slack and Notion apply this autocorrect.",
        )

    single = _outside(_SINGLE_QUOTE_KEY, text, scan.spans)
    if single is not None:
        quote = text.index("'", single.start(), single.end())
        add(
            quote,
            "single-quotes",
            "single-quoted string",
            'JSON requires double quotes: {"key": "value"}.',
        )

    comment = _outside(_COMMENT, text, scan.spans)
    if comment is not None:
        kind = "// line comment" if comment.group() == "//" else "/* block comment */"
        add(
            comment.start(),
            "comments",
            f"{kind} is not valid JSON",
            "remove the comment, or run the file through a JSONC parser first.",
        )

    non_finite = _outside(_NON_FINITE, text, scan.spans)
    if non_finite is not None:
        add(
            non_finite.start(),
            "non-finite",
            f"{non_finite.group()} is not valid JSON",
            "use null, or a quoted string and convert it downstream.",
            preview=non_finite.group(),
        )

    trailing = _outside(_TRAILING_COMMA, text, scan.spans)
    if trailing is not None:
        pos = trailing.start()
        line, col = _line_col(text, pos)
        add(
            pos,
            "trailing-comma",
            "trailing comma before a closing bracket",
            f"remove the comma at line {line}, column {col}.",
        )

    # A newline inside a string is its own defect, unless the payload simply stops
    # there: then the cut is the root cause and the newline is an artifact of it.
    if scan.unescaped_newline is not None and not truncated:
        add(
            scan.unescaped_newline,
            "unescaped-newline",
            "line break inside a string",
            "use a literal \\n escape; JSON strings cannot span lines.",
        )

    if truncated:
        if scan.unterminated_string is not None:
            pos = scan.unterminated_string
            message = "unclosed string"
        else:
            opener, pos = scan.open_stack[-1]
            message = f"unclosed {opener!r}"
        line, col = _line_col(text, pos)
        hint = f"opened at line {line}, column {col}; the payload looks cut off."
        if scan.open_stack and scan.unterminated_string is not None:
            hint += " Brackets are still open too."
        add(pos, "truncation", message, hint)

    if scan.mismatched is not None:
        closer, pos, expected = scan.mismatched
        line, col = _line_col(text, pos)
        add(
            pos,
            "mismatched-bracket",
            f"{closer!r} where a closing {expected!r} was expected",
            f"check bracket pairing around line {line}, column {col}.",
        )

    if not candidates:
        pos = error.pos if error.pos is not None else 0
        short = error.msg.split(":")[0].strip().lower()
        candidates.append(
            (
                pos,
                _finding(
                    "invalid-json",
                    f"invalid JSON: {short}",
                    payload_id,
                    pos=pos,
                    text=text,
                    hint="the position above is where the parser stopped, not necessarily where "
                    "the mistake started.",
                ),
            )
        )

    candidates.sort(key=lambda item: item[0])
    return [finding for _, finding in candidates]


def _scan(text: str) -> _Scan:
    """One pass over the text: string spans, bracket stack, newline-in-string."""
    scan = _Scan()
    in_string = False
    string_start = 0
    index = 0
    length = len(text)
    while index < length:
        char = text[index]
        if in_string:
            if char == "\\":
                index += 2  # skip the escaped character, whatever it is
                continue
            if char == '"':
                in_string = False
                scan.spans.append((string_start, index))
            elif char == "\n" and scan.unescaped_newline is None:
                scan.unescaped_newline = index
            index += 1
            continue
        if char == '"':
            in_string = True
            string_start = index
        elif char in "{[":
            scan.open_stack.append((char, index))
        elif char in "}]":
            expected = "{" if char == "}" else "["
            if scan.open_stack and scan.open_stack[-1][0] == expected:
                scan.open_stack.pop()
            elif scan.open_stack:
                if scan.mismatched is None:
                    scan.mismatched = (char, index, scan.open_stack[-1][0])
                scan.open_stack.pop()
        index += 1
    if in_string:
        scan.unterminated_string = string_start
    return scan


def _first_smart_quote(text: str, spans: list[tuple[int, int]]) -> tuple[int, str] | None:
    """Earliest curly quote that is not inside a properly quoted JSON string."""
    best: tuple[int, str] | None = None
    for char in SMART_QUOTES:
        pos = text.find(char)
        while pos != -1 and _inside(pos, spans):
            pos = text.find(char, pos + 1)
        if pos != -1 and (best is None or pos < best[0]):
            best = (pos, char)
    return best


def _outside(
    pattern: re.Pattern[str], text: str, spans: list[tuple[int, int]]
) -> re.Match[str] | None:
    """First regex match that starts outside every JSON string span."""
    for match in pattern.finditer(text):
        if not _inside(match.start(), spans):
            return match
    return None


def _inside(pos: int, spans: list[tuple[int, int]]) -> bool:
    """Is ``pos`` strictly between a string's opening and closing quote?

    ``spans`` is sorted by start, so this is a binary search rather than a scan:
    a 10,000-record payload has tens of thousands of strings.
    """
    if not spans:
        return False
    index = bisect_right(spans, (pos,)) - 1
    return index >= 0 and spans[index][1] > pos


def _line_col(text: str, pos: int) -> tuple[int, int]:
    """1-based line and column for a character offset."""
    pos = max(0, min(pos, len(text)))
    line = text.count("\n", 0, pos) + 1
    last_break = text.rfind("\n", 0, pos)
    return line, pos - last_break


def _finding(
    rule: str,
    message: str,
    payload_id: str,
    *,
    pos: int | None = None,
    text: str | None = None,
    hint: str | None = None,
    preview: str | None = None,
) -> Finding:
    line = col = None
    if text is not None and pos is not None:
        line, col = _line_col(text, pos)
    return Finding(
        layer=Layer.SYNTAX,
        rule=rule,
        severity=Severity.ERROR,
        path="$",
        message=message,
        hint=hint,
        value_preview=preview,
        payload_id=payload_id,
        line=line,
        col=col,
    )
