"""Import log writer — records each scrape run for auditing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import asyncpg

logger = structlog.get_logger()


async def fetch_silent_failures(
    pool: asyncpg.Pool,
    *,
    since_hours: int = 2,
    min_historical_events: int = 10,
) -> list[dict]:
    """Find sources that returned 0 events this run but were previously productive.

    A "silent failure" is a source that:
    - Completed without error in the last `since_hours`
    - Found 0 raw events (events_found = 0)
    - Previously found >= `min_historical_events` in some historical run

    These are the most dangerous failures because they produce no error output —
    the scrape appears to succeed while events quietly stop flowing.

    Args:
        pool: asyncpg connection pool.
        since_hours: How far back to look for the "current run" window.
        min_historical_events: Minimum peak historical events to consider a source established.

    Returns:
        List of dicts with keys: source_id, source_name, latest_run, peak_events_found.
    """
    sql = """
    WITH recent AS (
        SELECT DISTINCT ON (source_id)
            source_id, source_name, events_found, status, created_at
        FROM import_logs
        WHERE created_at > NOW() - ($1 * INTERVAL '1 hour')
        ORDER BY source_id, created_at DESC
    ),
    historical AS (
        SELECT source_id, MAX(events_found) AS peak_events_found
        FROM import_logs
        WHERE created_at <= NOW() - ($1 * INTERVAL '1 hour')
        GROUP BY source_id
    )
    SELECT
        r.source_id,
        r.source_name,
        r.created_at   AS latest_run,
        h.peak_events_found
    FROM recent r
    JOIN historical h ON r.source_id = h.source_id
    WHERE r.events_found = 0
      AND r.status = 'completed'
      AND h.peak_events_found >= $2
    ORDER BY h.peak_events_found DESC
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, since_hours, min_historical_events)
    return [dict(r) for r in rows]


async def write_import_log(
    pool: asyncpg.Pool,
    *,
    source_id: str,
    source_name: str,
    events_found: int,
    events_imported: int,
    events_updated: int,
    events_skipped: int,
    events_errors: int,
    duration_ms: int,
    status: str = "completed",
    error_message: str | None = None,
) -> None:
    """Write a record to the import_logs table.

    Args:
        pool: asyncpg connection pool.
        source_id: Source config ID.
        source_name: Human-readable source name.
        events_found: Total raw events scraped.
        events_imported: New events inserted.
        events_updated: Existing events updated.
        events_skipped: Events skipped by quality gates.
        events_errors: Events that failed to upsert.
        duration_ms: Total scrape duration in milliseconds.
        status: 'completed', 'partial', or 'failed'.
        error_message: Error details if status != 'success'.
    """
    try:
        async with pool.acquire() as conn:
            await conn.execute(
                """
                INSERT INTO import_logs (
                    source_id, source_name,
                    events_found, events_imported, events_updated,
                    events_skipped, events_errors,
                    duration_ms, status, error_message,
                    created_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)
                """,
                source_id,
                source_name,
                events_found,
                events_imported,
                events_updated,
                events_skipped,
                events_errors,
                duration_ms,
                status,
                error_message,
                datetime.now(UTC),
            )
            logger.info(
                "import_log_written",
                source_id=source_id,
                status=status,
                imported=events_imported,
                updated=events_updated,
            )
    except Exception:
        logger.exception("failed_to_write_import_log", source_id=source_id)
