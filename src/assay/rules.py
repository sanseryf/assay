"""Rule configuration: YAML in, validated models out.

A typo in a rule file must fail at startup with a message that names the field, not
produce a rule that silently never fires. So the models forbid unknown keys, validate
every path expression, and are built from a discriminated union on ``kind``.

The file looks like this::

    version: 1
    duplicate_keys: true
    strings:
      max_length:
        "$.members[*].first_name": 40
    rules:
      - id: dates-ordered
        kind: compare
        left: "$.members[*].start_date"
        op: "<="
        right: "$.members[*].end_date"
        severity: error
        hint: "End date precedes start date. Check timezone or field mapping."
"""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    ValidationError,
    field_validator,
    model_validator,
)

from .jsonpath import PathError, parse
from .models import Severity, coerce_severity

DEFAULT_PLACEHOLDERS: tuple[str, ...] = (
    "test",
    "testing",
    "tbd",
    "tba",
    "todo",
    "n/a",
    "na",
    "none",
    "null",
    "nil",
    "string",
    "xxx",
    "changeme",
    "unknown",
    "0000000000",
)


class ConfigError(Exception):
    """The rules file is unusable. The CLI turns this into exit code 3."""


class _Rule(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: str = Field(min_length=1)
    severity: Severity = Severity.ERROR
    hint: str | None = None
    message: str | None = None

    @field_validator("severity", mode="before", check_fields=False)
    @classmethod
    def _lowercase_severity(cls, value: object) -> object:
        return coerce_severity(value) if isinstance(value, str) else value

    @field_validator("path", "left", "right", "key", check_fields=False)
    @classmethod
    def _valid_path(cls, value: str) -> str:
        try:
            parse(value)
        except PathError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("paths", check_fields=False)
    @classmethod
    def _valid_paths(cls, value: list[str] | None) -> list[str] | None:
        for item in value or ():
            try:
                parse(item)
            except PathError as exc:
                raise ValueError(str(exc)) from exc
        return value


class CompareRule(_Rule):
    kind: Literal["compare"]
    left: str
    op: Literal["<=", "<", ">=", ">", "==", "!="]
    right: str


class RequiredIfRule(_Rule):
    kind: Literal["required_if"]
    path: str
    equals: str | int | bool
    require: str = Field(min_length=1)


class ConstantSmellRule(_Rule):
    kind: Literal["constant_smell"]
    path: str
    threshold: float = Field(default=0.95, ge=0.0, le=1.0)
    min_records: int = Field(default=20, ge=2)
    severity: Severity = Severity.WARNING


class PlaceholderRule(_Rule):
    kind: Literal["placeholder"]
    paths: list[str] | None = None
    values: list[str] = Field(default_factory=lambda: list(DEFAULT_PLACEHOLDERS))
    severity: Severity = Severity.WARNING


class CaseDriftRule(_Rule):
    kind: Literal["case_drift"]
    path: str
    min_records: int = Field(default=3, ge=2)
    severity: Severity = Severity.WARNING


class DuplicateRecordsRule(_Rule):
    kind: Literal["duplicate_records"]
    key: str


class TypeInconsistencyRule(_Rule):
    kind: Literal["type_inconsistency"]
    paths: list[str] | None = None
    min_records: int = Field(default=5, ge=2)
    severity: Severity = Severity.WARNING


Rule = Annotated[
    CompareRule
    | RequiredIfRule
    | ConstantSmellRule
    | PlaceholderRule
    | CaseDriftRule
    | DuplicateRecordsRule
    | TypeInconsistencyRule,
    Field(discriminator="kind"),
]


class StringsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    max_length: dict[str, int] = Field(default_factory=dict)

    @field_validator("max_length")
    @classmethod
    def _valid_paths(cls, value: dict[str, int]) -> dict[str, int]:
        for expr in value:
            try:
                parse(expr)
            except PathError as exc:
                raise ValueError(str(exc)) from exc
        return value


class GeoAddressConfig(BaseModel):
    """One address field in the payload, and what the destination expects of it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str
    residential: bool = True
    line1_max: int | None = Field(default=None, ge=1)
    household_key: str | None = None

    @field_validator("path")
    @classmethod
    def _valid_path(cls, value: str) -> str:
        try:
            parse(value)
        except PathError as exc:
            raise ValueError(str(exc)) from exc
        return value

    @field_validator("household_key")
    @classmethod
    def _valid_optional_path(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                parse(value)
            except PathError as exc:
                raise ValueError(str(exc)) from exc
        return value


class GeoConfig(BaseModel):
    """Offline geography. With no addresses configured, the layer does nothing."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    enabled: bool = True
    addresses: tuple[GeoAddressConfig, ...] = ()
    employer_address: str | None = None

    @field_validator("employer_address")
    @classmethod
    def _valid_employer_path(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                parse(value)
            except PathError as exc:
                raise ValueError(str(exc)) from exc
        return value

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.addresses)


class RulesConfig(BaseModel):
    """A whole rules file. Every field is optional; an empty file is valid."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    duplicate_keys: bool = True
    strings: StringsConfig = Field(default_factory=StringsConfig)
    geo: GeoConfig = Field(default_factory=GeoConfig)
    rules: tuple[Rule, ...] = ()

    @model_validator(mode="after")
    def _unique_rule_ids(self) -> RulesConfig:
        identifiers = [rule.id for rule in self.rules]
        duplicates = {item for item in identifiers if identifiers.count(item) > 1}
        if duplicates:
            raise ValueError(f"rule ids must be unique; duplicated: {sorted(duplicates)}")
        return self


EMPTY_CONFIG = RulesConfig()


def load_config(path: Path) -> RulesConfig:
    """Read and validate a rules file, raising :class:`ConfigError` with the reason."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {path}: {exc.strerror or exc}") from exc
    try:
        document = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise ConfigError(f"{path} is not valid YAML: {exc}") from exc
    if document is None:
        return RulesConfig()
    if not isinstance(document, dict):
        raise ConfigError(f"{path} must contain a mapping at the top level")
    try:
        return RulesConfig.model_validate(document)
    except ValidationError as exc:
        details = "; ".join(
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()
        )
        raise ConfigError(f"invalid rules in {path}: {details}") from exc


def rules_by_kind(config: RulesConfig, kind: str) -> list[_Rule]:
    """Rules of one kind, in file order."""
    return [rule for rule in config.rules if rule.kind == kind]
