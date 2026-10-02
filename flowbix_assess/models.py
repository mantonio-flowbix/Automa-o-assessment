from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Severity(str, Enum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
    MANUAL_REVIEW = "manual_review"


SEVERITY_ORDER = {
    Severity.CRITICAL: 0,
    Severity.WARNING: 1,
    Severity.INFO: 2,
    Severity.MANUAL_REVIEW: 3,
}


@dataclass
class Finding:
    section: str
    title: str
    severity: Severity
    description: str
    recommendation: str
    evidence: dict = field(default_factory=dict)
    estimated_effort_hours: float | None = None
    # Suggested days (from report generation date) to resolve this finding —
    # judged per topic, not a flat per-severity bucket (a saturated disk and
    # a cosmetic dashboard name both being "warning" doesn't mean they're
    # equally urgent). None = purely informational, nothing to schedule.
    deadline_days: int | None = None


@dataclass
class CollectionResult:
    """Container for everything pulled from a single data source.

    `errors` holds source name -> error message for sources that failed or
    were not configured, so the report can say what it could not check
    instead of silently omitting it.
    """

    zabbix: dict = field(default_factory=dict)
    mysql: dict = field(default_factory=dict)
    infra: dict = field(default_factory=dict)
    grafana: dict = field(default_factory=dict)
    errors: dict = field(default_factory=dict)
    mysql_size_history: list = field(default_factory=list)
