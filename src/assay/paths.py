"""Path helpers shared by the layers.

Findings carry paths without a leading ``$`` (``members[3].dob``), because the
report prints them under a ``WHERE`` column and the dollar sign is noise there.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence

Part = str | int


def format_path(parts: Sequence[Part]) -> str:
    """``['members', 3, 'dob']`` -> ``members[3].dob``; root -> ``$``."""
    if not parts:
        return "$"
    out = ""
    for part in parts:
        if isinstance(part, int):
            out += f"[{part}]"
        else:
            out += ("." if out else "") + part
    return out


def join(parent: str, field: str) -> str:
    """Append a field name to a path, handling the root case."""
    if parent in {"$", ""}:
        return field
    return f"{parent}.{field}"


def walk(value: object, parts: tuple[Part, ...] = ()) -> Iterator[tuple[str, object]]:
    """Yield ``(path, value)`` for every node, depth first, root included.

    Iterative on purpose: a 3,000-level payload must produce an ``input-too-deep``
    finding, not a ``RecursionError`` from the walk that looks for duplicate keys.
    """
    stack: list[tuple[object, tuple[Part, ...]]] = [(value, parts)]
    while stack:
        node, trail = stack.pop()
        yield format_path(trail), node
        children: list[tuple[object, tuple[Part, ...]]] = []
        if isinstance(node, dict):
            children = [(child, (*trail, str(key))) for key, child in node.items()]
        elif isinstance(node, list):
            children = [(child, (*trail, index)) for index, child in enumerate(node)]
        stack.extend(reversed(children))
