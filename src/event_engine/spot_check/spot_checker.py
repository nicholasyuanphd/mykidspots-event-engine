# src/event_engine/spot_check/spot_checker.py
"""Phase 2: Weekly random spot-check for verified CVB sources."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

from event_engine.classify.ai_classifier import (
    AIClassifier,
    ClassificationResult,
    TOURISM_SYSTEM_PROMPT,
)
from event_engine.models import load_sources
from event_engine.spot_check.db import (
    fetch_random_active_events,
    fetch_verified_cvb_sources,
    upsert_trust_override,
)

logger = structlog.get_logger()

_SAMPLE_SIZE = 5
_FAIL_THRESHOLD = 0.4  # >=40% failing -> downgrade


@dataclass
class SpotCheckResult:
    """Aggregate result of a weekly spot-check run."""
    sources_checked: int = 0
    sources_downgraded: int = 0
    source_results: dict[str, dict] = field(default_factory=dict)


def _domain_for_source(source_id: str, sources_dir: Path | None) -> str | None:
    """Look up the domain (netloc) for a source_id from YAML configs.

    Returns None if the source_id is not found.
    """
    from urllib.parse import urlparse

    if sources_dir is None:
        return None

    sources = load_sources(sources_dir)
    source = next((s for s in sources if s.id == source_id), None)
    if source is None:
        return None
    return urlparse(source.base_url).netloc


async def run_spot_check(
    pool,
    classifier: AIClassifier,
    sources_dir: Path | None,
) -> SpotCheckResult:
    """Run the weekly spot-check across all verified CVB sources."""
    result = SpotCheckResult()
    verified_source_ids = await fetch_verified_cvb_sources(pool)

    logger.info("spot_check_started", sources=verified_source_ids)

    for source_id in verified_source_ids:
        log = logger.bind(source_id=source_id)
        result.sources_checked += 1

        domain = _domain_for_source(source_id, sources_dir)
        if not domain:
            log.warning("spot_check_domain_not_found", source_id=source_id)
            result.source_results[source_id] = {"skipped": True, "reason": "domain_not_found"}
            continue

        events = await fetch_random_active_events(pool, domain=domain, limit=_SAMPLE_SIZE)

        if not events:
            log.info("spot_check_no_active_events", domain=domain)
            result.source_results[source_id] = {"skipped": True, "reason": "no_active_events"}
            continue

        titles = [e["title"] for e in events]
        verdicts = await classifier.classify_batch(titles, system_prompt=TOURISM_SYSTEM_PROMPT)

        failed = sum(1 for v in verdicts if v == ClassificationResult.NO)
        total = len(verdicts)
        fail_rate = failed / total if total > 0 else 0.0

        for event, verdict in zip(events, verdicts):
            log.info(
                "spot_check_event_result",
                title=event["title"],
                verdict=verdict.value,
                source_url=event["source_url"],
            )

        log.info(
            "spot_check_source_summary",
            total=total,
            failed=failed,
            fail_rate=round(fail_rate, 2),
        )

        if fail_rate >= _FAIL_THRESHOLD:
            log.warning(
                "source_downgraded",
                fail_rate=round(fail_rate, 2),
                threshold=_FAIL_THRESHOLD,
                note="Future ingestion runs will land events as pending. Existing active events unchanged.",
            )
            await upsert_trust_override(pool, source_id, "new")
            result.sources_downgraded += 1
            result.source_results[source_id] = {
                "downgraded": True,
                "fail_rate": round(fail_rate, 2),
                "total": total,
                "failed": failed,
            }
        else:
            result.source_results[source_id] = {
                "downgraded": False,
                "fail_rate": round(fail_rate, 2),
                "total": total,
                "failed": failed,
            }

    logger.info(
        "spot_check_complete",
        sources_checked=result.sources_checked,
        sources_downgraded=result.sources_downgraded,
    )
    return result
