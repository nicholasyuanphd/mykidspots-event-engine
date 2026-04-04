# src/event_engine/spot_check/auditor.py
"""Phase 1: One-time source graduation audit."""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from event_engine.classify.ai_classifier import (
    AIClassifier,
    ClassificationResult,
    TOURISM_SYSTEM_PROMPT,
)
from event_engine.spot_check.db import (
    fetch_random_pending_events,
    upsert_trust_override,
    bulk_activate_pending_events,
)

logger = structlog.get_logger()

_SAMPLE_SIZE = 10
_PASS_THRESHOLD = 0.8  # >=80% must pass to graduate


@dataclass
class AuditResult:
    """Result of a graduation audit for one source."""
    source_id: str
    total: int
    passed: int
    graduated: bool
    events_activated: int = 0
    verdicts: list[str] = field(default_factory=list)


async def audit_source(
    pool,
    classifier: AIClassifier,
    source_id: str,
    domain: str,
) -> AuditResult:
    """Run the graduation audit for a single source."""
    log = logger.bind(source_id=source_id, domain=domain)

    events = await fetch_random_pending_events(pool, domain=domain, limit=_SAMPLE_SIZE)

    if not events:
        log.warning("audit_no_pending_events")
        return AuditResult(source_id=source_id, total=0, passed=0, graduated=False)

    titles = [e["title"] for e in events]
    verdicts = await classifier.classify_batch(titles, system_prompt=TOURISM_SYSTEM_PROMPT)

    passed = sum(1 for v in verdicts if v != ClassificationResult.NO)
    total = len(verdicts)
    pass_rate = passed / total if total > 0 else 0.0

    log.info(
        "audit_complete",
        total=total,
        passed=passed,
        pass_rate=round(pass_rate, 2),
        verdicts=[v.value for v in verdicts],
    )

    for event, verdict in zip(events, verdicts):
        log.info(
            "audit_event_result",
            title=event["title"],
            verdict=verdict.value,
            source_url=event["source_url"],
        )

    if pass_rate < _PASS_THRESHOLD:
        log.warning(
            "audit_failed_threshold",
            pass_rate=round(pass_rate, 2),
            required=_PASS_THRESHOLD,
        )
        return AuditResult(
            source_id=source_id,
            total=total,
            passed=passed,
            graduated=False,
            verdicts=[v.value for v in verdicts],
        )

    await upsert_trust_override(pool, source_id, "verified")
    activated = await bulk_activate_pending_events(pool, domain)

    log.info("source_graduated", events_activated=activated)

    return AuditResult(
        source_id=source_id,
        total=total,
        passed=passed,
        graduated=True,
        events_activated=activated,
        verdicts=[v.value for v in verdicts],
    )
