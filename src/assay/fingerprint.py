"""Fingerprints: collapse per-record findings to per-root-cause groups.

The fingerprint is ``layer:rule:normalized-path:signature`` and deliberately
contains **no values**. That is what lets ``members[847].zip`` and
``members[12].zip`` land in one group, and it is also why redaction (a later
milestone) cannot change how a batch is grouped.
"""

from __future__ import annotations

import re

from .models import Finding

_INDEX = re.compile(r"\[\d+\]")


def normalize_path(path: str) -> str:
    """Replace every array index with ``[*]`` so records collapse together."""
    return _INDEX.sub("[*]", path)


def fingerprint(finding: Finding) -> str:
    """Build the stable identity of a finding's root cause."""
    parts = [finding.layer.value, finding.rule, normalize_path(finding.path)]
    if finding.signature:
        parts.append(finding.signature)
    return ":".join(part for part in parts if part)
