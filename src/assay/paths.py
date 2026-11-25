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
    """Yield ``(path, value)`` for every node, depth first, root included."""
    yield format_path(parts), value
    if isinstance(value, dict):
        for key, child in value.items():
            yield from walk(child, (*parts, str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            yield from walk(child, (*parts, index))
