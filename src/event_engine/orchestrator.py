"""Orchestrator — coordinates scraping across all sources with concurrency control."""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime
from typing import TYPE_CHECKING

import httpx
import structlog

from event_engine.circuit_breaker import CircuitBreaker
from event_engine.db.connection import create_pool
from event_engine.db.hygiene import run_hygiene_scan
from event_engine.db.import_log import write_import_log
from event_engine.db.upsert import upsert_batch
from event_engine.models import SourceConfig, load_sources
from event_engine.normalize.pipeline import normalize
from event_engine.reporting import RunReport, SourceResult
from event_engine.scrapers.registry import discover_scrapers, get_scraper

if TYPE_CHECKING:
    import asyncpg

logger = structlog.get_logger()


async def run(
    database_url: str,
    sources_dir: str,
    source_filter: str | None = None,
    dry_run: bool = False,
    max_concurrency: int = 5,
) -> RunReport:
    """Run the event engine — scrape all sources and upsert events.

    Args:
        database_url: PostgreSQL connection string.
        sources_dir: Path to directory containing YAML source configs.
        source_filter: Optional glob pattern to filter source IDs.
        dry_run: If True, scrape and normalize but skip DB writes.
        max_concurrency: Maximum concurrent source scrapes.

    Returns:
        RunReport summarizing the results.
    """
    from pathlib import Path

    report = RunReport(dry_run=dry_run)

    # Discover all available scrapers
    discover_scrapers()

    # Load source configs
    sources = load_sources(Path(sources_dir), source_filter)
    logger.info("sources_loaded", count=len(sources), filter=source_filter)

    if not sources:
        logger.warning("no_sources_found", sources_dir=sources_dir, filter=source_filter)
        report.finished_at = datetime.now(UTC)
        return report

    # Create DB pool (skip if dry run)
    pool: asyncpg.Pool | None = None
    if not dry_run:
        pool = await create_pool(database_url)
        await run_hygiene_scan(pool)

    # Set up concurrency control
    semaphore = asyncio.Semaphore(max_concurrency)
    cb = CircuitBreaker()

    # Fan out across sources
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(30.0, connect=10.0),
        follow_redirects=True,
        headers={"User-Agent": "MyKidSpots-EventEngine/0.1 (contact@yapplify.com)"},
    ) as client:
        tasks = [_scrape_source(source, client, pool, semaphore, cb, dry_run) for source in sources]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    # Collect results
    for i, result in enumerate(results):
        if isinstance(result, Exception):
            source = sources[i]
            report.sources.append(
                SourceResult(
                    source_id=source.id,
                    source_name=source.name,
                    status="failed",
                    error_message=str(result),
                )
            )
            logger.exception("source_failed", source_id=source.id, error=str(result))
        elif isinstance(result, SourceResult):
            report.sources.append(result)

    # Write import logs
    if pool and not dry_run:
        for sr in report.sources:
            await write_import_log(
                pool,
                source_id=sr.source_id,
                source_name=sr.source_name,
                events_found=sr.events_found,
                events_imported=sr.events_inserted,
                events_updated=sr.events_updated,
                events_skipped=sr.events_found - sr.events_normalized,
                events_errors=sr.events_errors,
                duration_ms=sr.duration_ms,
                status=sr.status,
                error_message=sr.error_message,
            )
        await pool.close()

    report.finished_at = datetime.now(UTC)
    return report


async def _scrape_source(
    source: SourceConfig,
    client: httpx.AsyncClient,
    pool: asyncpg.Pool | None,
    semaphore: asyncio.Semaphore,
    cb: CircuitBreaker,
    dry_run: bool,
) -> SourceResult:
    """Scrape a single source with concurrency and circuit breaker control."""
    result = SourceResult(source_id=source.id, source_name=source.name)
    start_time = time.monotonic()

    async with semaphore:
        log = logger.bind(source_id=source.id, source_name=source.name)

        # Check circuit breaker
        if not cb.can_execute(source.id):
            result.status = "failed"
            result.error_message = "Circuit breaker open"
            log.warning("circuit_breaker_open")
            return result

        try:
            # Get the right scraper for this platform
            scraper_cls = get_scraper(source.platform)
            scraper = scraper_cls(source, client)

            # Scrape raw events
            log.info("scraping_source")
            raw_events = await scraper.scrape_all()
            result.events_found = len(raw_events)
            log.info("raw_events_scraped", count=len(raw_events))

            # Normalize
            normalized = []
            for raw in raw_events:
                event = normalize(raw, source)
                if event:
                    normalized.append(event)
            result.events_normalized = len(normalized)
            skipped = len(raw_events) - len(normalized)
            log.info("events_normalized", count=len(normalized), skipped=skipped)

            # Upsert to database
            if pool and not dry_run and normalized:
                counts = await upsert_batch(pool, normalized)
                result.events_inserted = counts["inserted"]
                result.events_updated = counts["updated"]
                result.events_unchanged = counts["unchanged"]
                result.events_errors = counts["errors"]
            elif dry_run:
                result.events_inserted = len(normalized)

            cb.record_success(source.id)
            log.info(
                "source_completed",
                found=result.events_found,
                normalized=result.events_normalized,
                inserted=result.events_inserted,
                updated=result.events_updated,
            )

        except Exception as e:
            cb.record_failure(source.id)
            result.status = "failed"
            result.error_message = str(e)
            log.exception("source_scrape_failed", error=str(e))

    result.duration_ms = int((time.monotonic() - start_time) * 1000)
    return result
