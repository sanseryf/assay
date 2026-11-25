"""A deliberately small JSONPath subset.

Assay's rules address fields with ``$.members[*].start_date``; that is all the path
language this tool needs. Supporting the whole of RFC 9535 would be a dependency and
a spec to track, and would still not answer the only question that matters here:
"which concrete values did this rule touch?"
"""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from typing import Any

from .paths import format_path

_TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_-]*)|\[(\d+|\*)\]")
WILDCARD = "*"

Token = str | int


class PathError(ValueError):
    """The path expression itself is malformed; that is a config error, not data."""


def parse(expr: str) -> list[Token]:
    """``$.members[*].dob`` -> ``['members', '*', 'dob']``."""
    text = expr.strip()
    if not text.startswith("$"):
        raise PathError(f"path must start with '$': {expr!r}")
    tokens: list[Token] = []
    position = 1
    while position < len(text):
        match = _TOKEN.match(text, position)
        if match is None:
            raise PathError(f"cannot parse {expr!r} at character {position}")
        name, index = match.groups()
        if name is not None:
            tokens.append(name)
        elif index == WILDCARD:
            tokens.append(WILDCARD)
        else:
            tokens.append(int(index))
        position = match.end()
    return tokens


def resolve(document: Any, path: str | Sequence[Token]) -> list[tuple[str, Any]]:
    """Every ``(path, value)`` the expression matches, in document order.

    Iterative for the same reason :func:`assay.paths.walk` is: rules must never turn
    a deeply nested payload into a ``RecursionError``.
    """
    tokens = parse(path) if isinstance(path, str) else list(path)
    found: list[tuple[str, Any]] = []
    stack: list[tuple[Any, int, tuple[Token, ...]]] = [(document, 0, ())]
    while stack:
        value, position, trail = stack.pop()
        if position == len(tokens):
            found.append((format_path(trail), value))
            continue
        token = tokens[position]
        children: list[tuple[Any, tuple[Token, ...]]] = []
        if token == WILDCARD:
            if isinstance(value, dict):
                children = [(child, (*trail, str(key))) for key, child in value.items()]
            elif isinstance(value, list):
                children = [(child, (*trail, index)) for index, child in enumerate(value)]
        elif isinstance(token, int):
            if isinstance(value, list) and -len(value) <= token < len(value):
                children = [(value[token], (*trail, token))]
        elif isinstance(value, dict) and token in value:
            children = [(value[token], (*trail, token))]
        stack.extend(
            (child, position + 1, child_trail) for child, child_trail in reversed(children)
        )
    return found


def sibling(path: str, field: str) -> str:
    """The path of a sibling field: ``members[3].type`` + relationship."""
    parent = path.rsplit(".", 1)[0] if "." in path else "$"
    return f"{parent}.{field}" if parent != "$" else field


def leaf_paths(document: Any) -> Iterator[tuple[str, Any]]:
    """Every scalar leaf, which is what type and case checks care about."""
    from .paths import walk

    for path, value in walk(document):
        if not isinstance(value, dict | list):
            yield path, value
