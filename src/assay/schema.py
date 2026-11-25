"""Layer 2: schema.

``Draft202012Validator.iter_errors`` yields every violation instead of raising on
the first one; the work here is turning each ``ValidationError`` into a finding a
person can act on: what was expected, what arrived, and the most likely fix.

The format checker is passed explicitly. jsonschema treats ``format`` as an
annotation unless one is supplied, which is the most common silent gap in JSON
Schema validation: ``"03/14/1990"`` passes a ``date`` field by default.
"""

from __future__ import annotations

import re
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker
from jsonschema.exceptions import ValidationError
from jsonschema.exceptions import best_match as closest_sub_error

from .hints import (
    FORMAT_EXAMPLES,
    closest_match,
    is_leading_zero_candidate,
    join_choices,
    json_type,
    preview,
)
from .models import Finding, Layer, Severity
from .paths import format_path, join

_QUOTED = re.compile(r"'([^']+)'")
_REQUIRED = re.compile(r"'([^']+)' is a required property")


def _preview_value(value: Any) -> str | None:
    """Preview for an unexpected field's value; containers are noise, not samples."""
    if isinstance(value, dict | list):
        return None
    return preview(value)


class SchemaLayer:
    """Check documents against one compiled JSON Schema."""

    def __init__(self, schema: dict[str, Any], *, schema_id: str = "schema") -> None:
        Draft202012Validator.check_schema(schema)
        self.schema = schema
        self.schema_id = schema_id
        # Compiled once and reused for every payload in a batch: compiling a
        # validator walks and resolves the whole schema.
        self.validator = Draft202012Validator(schema, format_checker=FormatChecker())

    def check(self, document: Any, payload_id: str = "") -> list[Finding]:
        """Every violation in the document, ordered by path for stable reports."""
        errors = self.validator.iter_errors(document)
        findings: list[Finding] = []
        for error in errors:
            findings.extend(self._findings(error, payload_id))
        findings.sort(key=lambda finding: (finding.path, finding.rule))
        return findings

    def _findings(self, error: ValidationError, payload_id: str) -> list[Finding]:
        """One schema error can describe several missing or unknown fields.

        ``required`` is emitted once per missing field, but ``additionalProperties``
        names every unexpected field in a single message, so both cases are expanded
        into one finding per field: a report has to point at a field, not a set.
        """
        parent = format_path(list(error.absolute_path))

        if error.validator == "required":
            missing = _missing_fields(error)
            return [self._make(error, payload_id, path=join(parent, name)) for name in missing]

        if error.validator in {"additionalProperties", "unevaluatedProperties"}:
            unknown = _unknown_fields(error)
            findings: list[Finding] = []
            for name in unknown:
                preview = None
                if isinstance(error.instance, dict):
                    preview = _preview_value(error.instance.get(name))
                findings.append(
                    self._make(
                        error,
                        payload_id,
                        path=join(parent, name),
                        value_preview=preview,
                    )
                )
            return findings

        return [self._make(error, payload_id, path=parent)]

    def _make(
        self,
        error: ValidationError,
        payload_id: str,
        *,
        path: str,
        value_preview: str | None = None,
    ) -> Finding:
        return Finding(
            layer=Layer.SCHEMA,
            rule=error.validator or "schema",
            severity=Severity.ERROR,
            path=path,
            message=_message(error),
            signature=_signature(error),
            hint=_hint(error, path),
            value_preview=value_preview if value_preview is not None else _preview(error),
            payload_id=payload_id,
        )


def _missing_fields(error: ValidationError) -> list[str]:
    """The field(s) this particular ``required`` error is about.

    jsonschema emits one error per missing property, but ``validator_value`` lists
    the whole ``required`` array, so the message is the only place that says which
    field this error refers to.
    """
    match = _REQUIRED.search(error.message)
    if match:
        return [match.group(1)]
    required = error.validator_value if isinstance(error.validator_value, list) else []
    instance = error.instance if isinstance(error.instance, dict) else {}
    return [name for name in required if name not in instance]


def _unknown_fields(error: ValidationError) -> list[str]:
    """Every field the object has that the schema does not allow.

    ``additionalProperties`` puts all unexpected names in one message, so they are
    read back out; if the message format ever changes, fall back to the difference
    between the instance and the schema's known properties.
    """
    names = _QUOTED.findall(error.message)
    if names:
        return names
    schema = error.schema if isinstance(error.schema, dict) else {}
    known = set(schema.get("properties", {}))
    patterns = list(schema.get("patternProperties", {}))
    return [
        name
        for name in (error.instance or {})
        if name not in known and not any(re.search(pattern, name) for pattern in patterns)
    ]


def _message(error: ValidationError) -> str:
    """A short, value-free description, suitable for a report column."""
    validator = error.validator
    expected = error.validator_value

    if validator == "type":
        expected_text = expected if isinstance(expected, str) else " or ".join(map(str, expected))
        return f"{json_type(error.instance)}, expected {expected_text}"
    if validator == "required":
        return "missing required field"
    if validator == "enum":
        return "value not in allowed set"
    if validator in {"additionalProperties", "unevaluatedProperties"}:
        return "unknown field"
    if validator == "format":
        return f"not a valid {expected}"
    if validator == "pattern":
        return "does not match the expected pattern"
    if validator == "minLength":
        return f"shorter than {expected} characters"
    if validator == "maxLength":
        return f"longer than {expected} characters"
    if validator == "minimum":
        return f"below the minimum of {expected}"
    if validator == "maximum":
        return f"above the maximum of {expected}"
    if validator == "exclusiveMinimum":
        return f"must be greater than {expected}"
    if validator == "exclusiveMaximum":
        return f"must be less than {expected}"
    if validator == "multipleOf":
        return f"not a multiple of {expected}"
    if validator == "uniqueItems":
        return "duplicate items in the array"
    if validator == "minItems":
        return f"fewer than {expected} items"
    if validator == "maxItems":
        return f"more than {expected} items"
    if validator in {"minProperties", "maxProperties"}:
        return f"{validator} violated ({expected})"
    if validator == "const":
        return "does not match the required constant"
    if validator == "dependentRequired":
        return "missing a field required by another field"
    if validator == "oneOf":
        return "matches more than one allowed variant"
    if validator == "anyOf":
        return "matches no allowed variant"
    if validator == "not":
        return "matches a forbidden variant"
    return f"violates {validator}"


def _signature(error: ValidationError) -> str:
    """The shape of the problem, without the value: groups must not split per record."""
    validator = error.validator
    expected = error.validator_value
    if validator == "type":
        expected_text = expected if isinstance(expected, str) else "|".join(map(str, expected))
        return f"type:{json_type(error.instance)}→{expected_text}"
    if validator == "format":
        return f"fmt:{expected}"
    if validator == "pattern":
        pattern = str(expected)
        return f"pattern:{pattern[:60]}"
    if validator in {"minLength", "maxLength", "minItems", "maxItems", "minimum", "maximum"}:
        return f"{validator}:{expected}"
    return str(validator)


def _hint(error: ValidationError, path: str) -> str | None:
    """The "what to do about it" line."""
    validator = error.validator
    expected = error.validator_value

    if validator == "type":
        if json_type(error.instance) in {"integer", "number"} and expected == "string":
            if is_leading_zero_candidate(path):
                return (
                    "a number here usually means leading zeros were stripped upstream "
                    "(07030 became 7030); send the field as a string."
                )
            return "wrap the value in quotes, or fix the serializer that dropped them."
        return "convert the value, or loosen the schema if the type really is mixed."

    if validator == "required":
        return "check the field mapping; a missing field here usually means an empty source column."

    if validator == "enum":
        allowed = [str(choice) for choice in expected] if isinstance(expected, list) else []
        hint = f"allowed values: {join_choices(allowed)}."
        suggestion = closest_match(str(error.instance), allowed)
        if suggestion:
            hint += f" Did you mean {suggestion!r}?"
        return hint

    if validator in {"additionalProperties", "unevaluatedProperties"}:
        unknown = _unknown_fields(error)
        schema = error.schema if isinstance(error.schema, dict) else {}
        properties = list(schema.get("properties", {}))
        if unknown and properties:
            suggestion = closest_match(unknown[0], properties)
            if suggestion:
                return f"unknown field {unknown[0]!r}. Did you mean {suggestion!r}?"
            return f"unknown field {unknown[0]!r}; allowed fields: {join_choices(properties)}."
        return "remove the field, or declare it in the schema."

    if validator == "format":
        example = FORMAT_EXAMPLES.get(str(expected))
        return f"expected {expected}" + (f" such as {example}" if example else "") + "."

    if validator == "pattern":
        return f"expected the value to match {expected!r}."

    if validator == "const":
        return f"expected {preview(expected)}."

    if validator == "dependentRequired":
        dependencies = error.validator_value if isinstance(error.validator_value, dict) else {}
        for trigger, required in dependencies.items():
            if isinstance(error.instance, dict) and trigger in error.instance:
                return f"when {trigger!r} is present, {join_choices(required)} must be too."
        return "fill in the fields this one depends on."

    if validator in {"oneOf", "anyOf"} and error.context:
        best = closest_sub_error(error.context)
        if best is not None:
            return f"closest variant failed with: {best.message}"

    if validator == "uniqueItems":
        return "deduplicate the array before sending it."

    return None


def _preview(error: ValidationError) -> str | None:
    """The offending value, so the report can show samples per group."""
    if error.validator in {"required", "uniqueItems", "dependentRequired"}:
        return None
    if isinstance(error.instance, dict | list):
        return None  # a preview of a whole object is noise, not a sample
    return preview(error.instance)
