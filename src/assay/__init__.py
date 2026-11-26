"""Assay: a JSON payload error identifier.

Four layers feed a grouper that reports root causes instead of thousands of identical
failures: syntax, schema, setup rules and string quality. Geographic checks (M5-M6)
are designed in ``ARCHITECTURE.md`` at the repository root and not built yet.
"""

from __future__ import annotations

from .geo.checks import GeoLayer
from .geo.store import GeoDataError, ZipStore
from .loader import InputError, Payload, read_payloads
from .models import Finding, Group, Layer, Report, Severity
from .pipeline import Limits, Pipeline, check_payloads
from .rules import ConfigError, RulesConfig, load_config
from .schema import SchemaLayer
from .setup import SetupLayer
from .syntax import SyntaxResult, analyze

__version__ = "1.2.0"

__all__ = [
    "ConfigError",
    "Finding",
    "GeoDataError",
    "GeoLayer",
    "Group",
    "InputError",
    "Layer",
    "Limits",
    "Payload",
    "Pipeline",
    "Report",
    "RulesConfig",
    "SchemaLayer",
    "SetupLayer",
    "Severity",
    "SyntaxResult",
    "ZipStore",
    "__version__",
    "analyze",
    "check_payloads",
    "load_config",
    "read_payloads",
]
