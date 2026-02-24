"""Import log writer — records each scrape run for auditing."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import asyncpg

logger = structlog.get_logger()


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
