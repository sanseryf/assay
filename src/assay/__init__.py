"""Assay: a JSON payload error identifier.

Layers 1 (syntax) and 2 (schema) feed a grouper that reports root causes instead of
thousands of identical failures. Layers 3-5 are designed in ``ARCHITECTURE.md`` at the
repository root and not built yet.
"""

from __future__ import annotations

from .loader import InputError, Payload, read_payloads
from .models import Finding, Group, Layer, Report, Severity
from .pipeline import Limits, Pipeline, check_payloads
from .schema import SchemaLayer
from .syntax import SyntaxResult, analyze

__version__ = "0.1.0"

__all__ = [
    "Finding",
    "Group",
    "InputError",
    "Layer",
    "Limits",
    "Payload",
    "Pipeline",
    "Report",
    "SchemaLayer",
    "Severity",
    "SyntaxResult",
    "__version__",
    "analyze",
    "check_payloads",
    "read_payloads",
]
