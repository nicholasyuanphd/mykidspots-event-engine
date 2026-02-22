"""Cross-origin duplicate detection — checks ALL sources, not just pipeline."""

import asyncpg
import structlog

log = structlog.get_logger()

CROSS_ORIGIN_SQL = """
SELECT id, title, source, start_datetime
FROM events
WHERE status NOT IN ('hidden', 'expired')
  AND source != $4
  AND location_name = $3
  AND start_datetime::date = $2::date
  AND lower(trim(title)) = lower(trim($1))
LIMIT 1
"""


async def find_cross_origin_duplicate(
    pool: asyncpg.Pool,
    title: str,
    start_date: str,
    location_name: str,
    exclude_source: str,
) -> dict | None:
    """Check if an event with same title+date+location exists from a different source.

    Returns the existing event row if found, None if safe to insert.
    The exclude_source prevents checking against the same pipeline source
    (fingerprint-based dedup handles same-source conflicts).
    """
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            CROSS_ORIGIN_SQL,
            title,
            start_date,
            location_name,
            exclude_source,
        )
        if row:
            log.info(
                "cross_origin_duplicate_found",
                title=title,
                existing_source=row["source"],
                pipeline_source=exclude_source,
            )
            return dict(row)
        return None
