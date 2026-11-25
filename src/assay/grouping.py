"""Grouping: the reason this tool exists.

One broken mapping produces thousands of identical findings. Grouping collapses
them by fingerprint, ranks the groups by how many records they touch, and notes
groups that always hit the same records (the signature of one upstream serializer
rather than two independent bugs).
"""

from __future__ import annotations

from collections.abc import Iterable

from .fingerprint import fingerprint, normalize_path
from .models import Finding, Group

# Two groups whose affected-record sets overlap this much are reported together.
CORRELATION_THRESHOLD = 0.9

# Fliers are noise, so correlation needs enough records to mean anything.
CORRELATION_MIN_PAYLOADS = 5

# Pairwise correlation is O(n^2); only the groups a person will actually read.
CORRELATION_MAX_GROUPS = 60


def group_findings(findings: Iterable[Finding], *, correlate: bool = True) -> list[Group]:
    """Collapse findings into ranked groups.

    Ordering is by impact (descending), then by how early the layer runs, then by
    finding count. A syntax error therefore outranks a string warning of the same
    size, which is the documented tie-break.
    """
    groups: dict[str, Group] = {}
    for finding in findings:
        key = fingerprint(finding)
        group = groups.get(key)
        if group is None:
            group = Group(
                fingerprint=key,
                layer=finding.layer,
                rule=finding.rule,
                path=normalize_path(finding.path),
                severity=finding.severity,
                message=finding.message,
                hint=finding.hint,
            )
            groups[key] = group
        group.add(finding)

    for group in groups.values():
        group.recompute_impact()

    ranked = sorted(groups.values(), key=_rank_key)
    if correlate:
        correlate_groups(ranked)
    return ranked


def _rank_key(group: Group) -> tuple[float, int, int, int, str, str]:
    return (
        -group.impact,
        group.layer.rank,
        group.severity.rank,
        -group.count,
        group.path,
        group.rule,
    )


def correlate_groups(
    groups: list[Group],
    *,
    threshold: float = CORRELATION_THRESHOLD,
    min_payloads: int = CORRELATION_MIN_PAYLOADS,
    max_groups: int = CORRELATION_MAX_GROUPS,
) -> None:
    """Record groups that co-occur in the same payloads.

    Overlap is the Jaccard index of the two affected-payload sets: ``|A & B| / |A | B|``.
    Groups that are perfectly contained in a much larger group do not correlate,
    because the union makes the score small; that is intentional, since a niche
    error inside a broad one is not evidence of a shared cause.
    """
    candidates = groups[:max_groups]
    for index, left in enumerate(candidates):
        if left.payloads_affected < min_payloads:
            continue
        for right in candidates[index + 1 :]:
            if right.payloads_affected < min_payloads:
                continue
            if right.path == left.path:
                # Same field, different problem. Saying the two co-occur would be
                # true for every pair and teach the reader nothing.
                continue
            union = len(left.payload_ids | right.payload_ids)
            if not union:
                continue
            overlap = len(left.payload_ids & right.payload_ids) / union
            if overlap >= threshold:
                if right.fingerprint not in left.correlated_with:
                    left.correlated_with.append(right.fingerprint)
                if left.fingerprint not in right.correlated_with:
                    right.correlated_with.append(left.fingerprint)


def overlap_ratio(left: Group, right: Group) -> float:
    """Jaccard overlap of two groups' affected payloads, for report wording."""
    union = len(left.payload_ids | right.payload_ids)
    return len(left.payload_ids & right.payload_ids) / union if union else 0.0
