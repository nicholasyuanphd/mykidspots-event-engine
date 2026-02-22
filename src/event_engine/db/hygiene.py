"""DB hygiene scan — detects pre-existing data quality issues before scraping."""

import asyncpg
import structlog

log = structlog.get_logger()

MANUAL_DUPES_SQL = """
SELECT COUNT(*) FROM (
    SELECT title, start_datetime::date, location_name
    FROM events
    WHERE source = 'manual' AND status = 'active'
    GROUP BY title, start_datetime::date, location_name
    HAVING COUNT(*) > 1
) dupes
"""

PAST_DATE_ACTIVE_SQL = """
SELECT COUNT(*) FROM events
WHERE status = 'active'
  AND start_datetime < NOW() - INTERVAL '1 day'
  AND is_recurring = false
"""


async def run_hygiene_scan(pool: asyncpg.Pool) -> dict[str, int]:
    """Scan DB for pre-existing quality issues. Logs warnings, does not modify data.

    Returns:
        Dict with counts: {'manual_duplicates': N, 'past_date_active': N}
    """
    async with pool.acquire() as conn:
        manual_dupes = await conn.fetchval(MANUAL_DUPES_SQL) or 0
        past_date = await conn.fetchval(PAST_DATE_ACTIVE_SQL) or 0

    if manual_dupes > 0:
        log.warning("hygiene_manual_duplicates_found", count=manual_dupes)
    if past_date > 0:
        log.warning("hygiene_past_date_active_found", count=past_date)

    log.info(
        "hygiene_scan_complete",
        manual_duplicates=manual_dupes,
        past_date_active=past_date,
    )
    return {"manual_duplicates": manual_dupes, "past_date_active": past_date}
