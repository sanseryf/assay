"""Getting payloads in: files, globs, directories, NDJSON and stdin.

A payload is bytes plus an id. Nothing is decoded here: layer 1 owns decoding so
that a broken encoding becomes a finding instead of a crash.
"""

from __future__ import annotations

import glob as globlib
import sys
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path

DEFAULT_MAX_BYTES = 32 * 1024 * 1024
NDJSON_SUFFIXES = {".ndjson", ".jsonl"}
JSON_SUFFIXES = {".json"}


class InputError(Exception):
    """The inputs cannot be read at all: a missing file, a glob that matched nothing."""


@dataclass(frozen=True, slots=True)
class Payload:
    """One document to check.

    ``oversized`` is set when the file was longer than the byte limit; the raw
    bytes are then only a prefix, and the pipeline reports it instead of parsing.
    """

    payload_id: str
    raw: bytes
    oversized: bool = False


def expand_paths(patterns: Sequence[str]) -> list[str]:
    """Resolve globs, directories and ``-`` into concrete file paths.

    Order is preserved and duplicates are dropped, so ``assay check a.json *.json``
    checks ``a.json`` once.
    """
    resolved: list[str] = []
    seen: set[str] = set()
    for pattern in patterns:
        if pattern == "-":
            candidates = ["-"]
        elif Path(pattern).is_dir():
            directory = Path(pattern)
            candidates = sorted(
                str(path)
                for path in directory.iterdir()
                if path.is_file() and path.suffix.lower() in JSON_SUFFIXES | NDJSON_SUFFIXES
            )
        else:
            candidates = sorted(globlib.glob(pattern))
            if not candidates and Path(pattern).is_file():
                candidates = [pattern]
        if not candidates:
            raise InputError(f"no files matched {pattern!r}")
        for candidate in candidates:
            if candidate not in seen:
                seen.add(candidate)
                resolved.append(candidate)
    return resolved


def read_payloads(
    patterns: Sequence[str],
    *,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> Iterator[Payload]:
    """Yield payloads in the order given.

    NDJSON files are split on line boundaries; every other file is one payload.
    Stdin (``-``) is always one payload.
    """
    for path in expand_paths(patterns):
        if path == "-":
            yield Payload(payload_id="stdin", raw=sys.stdin.buffer.read())
            continue
        yield from _read_file(Path(path), max_bytes=max_bytes)


def _read_file(path: Path, *, max_bytes: int) -> Iterator[Payload]:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise InputError(f"cannot read {path}: {exc.strerror or exc}") from exc

    oversized = len(raw) > max_bytes
    if oversized:
        raw = raw[:max_bytes]

    display_id = _display_id(path)
    if path.suffix.lower() not in NDJSON_SUFFIXES:
        yield Payload(payload_id=display_id, raw=raw, oversized=oversized)
        return

    for number, line in enumerate(raw.splitlines(), start=1):
        if not line.strip():
            continue
        yield Payload(payload_id=f"{display_id}:{number}", raw=line)


def _display_id(path: Path) -> str:
    """The shortest stable name for a payload: relative to the cwd when possible.

    An absolute path in the report's WHERE column pushes everything else off the
    table. Uniqueness still holds because the relative path keeps its directories,
    and forward slashes keep report output identical across platforms.
    """
    try:
        return path.resolve().relative_to(Path.cwd()).as_posix()
    except (ValueError, OSError):
        return path.as_posix()
