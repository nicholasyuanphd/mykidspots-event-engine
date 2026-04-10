"""Database upsert operations for normalized events."""

from datetime import date

import asyncpg
import structlog

from event_engine.dedup.cross_origin import find_cross_origin_duplicate
from event_engine.models import NormalizedEvent

logger = structlog.get_logger()

# SQL for upserting a single event using the source_fingerprint unique index.
# ON CONFLICT updates mutable fields; the WHERE clause prevents unnecessary
# updated_at bumps when nothing actually changed.
UPSERT_SQL = """
INSERT INTO events (
    title, description, start_datetime, end_datetime,
    is_recurring, recurrence_pattern, recurrence_days_of_week,
    recurrence_end_date, recurrence_interval,
    location_name, address, city, latitude, longitude, timezone,
    category, age_min, age_max,
    cost_type, cost_amount, cost_details,
    image_urls, source, source_url, source_fingerprint,
    registration_url, status, visibility,
    series_key,
    upvotes, downvotes, view_count, save_count, click_count,
    created_at, updated_at
)
VALUES (
    $1, $2, $3, $4,
    $5, $6, $7,
    $8, $9,
    $10, $11, $12, $13, $14, $15,
    $16, $17, $18,
    $19, $20, $21,
    $22, $23, $24, $25,
    $26, $27, $28,
    $29,
    0, 0, 0, 0, 0,
    NOW(), NOW()
)
ON CONFLICT (source_fingerprint)
WHERE source_fingerprint IS NOT NULL AND status != 'expired'
DO UPDATE SET
    title = EXCLUDED.title,
    description = EXCLUDED.description,
    start_datetime = EXCLUDED.start_datetime,
    end_datetime = EXCLUDED.end_datetime,
    is_recurring = EXCLUDED.is_recurring,
    recurrence_pattern = EXCLUDED.recurrence_pattern,
    recurrence_days_of_week = EXCLUDED.recurrence_days_of_week,
    recurrence_end_date = EXCLUDED.recurrence_end_date,
    recurrence_interval = EXCLUDED.recurrence_interval,
    location_name = CASE WHEN events.location_locked THEN events.location_name ELSE EXCLUDED.location_name END,
    address       = CASE WHEN events.location_locked THEN events.address       ELSE EXCLUDED.address       END,
    latitude      = CASE WHEN events.location_locked THEN events.latitude      ELSE EXCLUDED.latitude      END,
    longitude     = CASE WHEN events.location_locked THEN events.longitude     ELSE EXCLUDED.longitude     END,
    category = EXCLUDED.category,
    age_min = EXCLUDED.age_min,
    age_max = EXCLUDED.age_max,
    cost_type = EXCLUDED.cost_type,
    cost_amount = EXCLUDED.cost_amount,
    cost_details = EXCLUDED.cost_details,
    image_urls = EXCLUDED.image_urls,
    source_url = EXCLUDED.source_url,
    registration_url = EXCLUDED.registration_url,
    status = EXCLUDED.status,
    series_key = EXCLUDED.series_key,
    updated_at = NOW()
WHERE events.title != EXCLUDED.title
   OR events.start_datetime != EXCLUDED.start_datetime
   OR events.description IS DISTINCT FROM EXCLUDED.description
   OR events.end_datetime IS DISTINCT FROM EXCLUDED.end_datetime
   OR events.location_name IS DISTINCT FROM EXCLUDED.location_name
   OR events.image_urls IS DISTINCT FROM EXCLUDED.image_urls
   OR events.source_url IS DISTINCT FROM EXCLUDED.source_url;
"""

# SQL to hide stale auto-imported events (disappeared from source)
HIDE_STALE_SQL = """
UPDATE events
SET status = 'hidden', updated_at = NOW()
WHERE source = 'auto-imported'
  AND source_fingerprint LIKE $1
  AND source_fingerprint != ALL($2::text[])
  AND status = 'active'
  AND updated_at < NOW() - INTERVAL '48 hours';
"""


async def upsert_event(pool: asyncpg.Pool, event: NormalizedEvent) -> str:
    """Upsert a single normalized event into the database.

    Returns 'inserted', 'updated', or 'unchanged'.
    """
    # Cross-origin dedup: skip if identical event exists from another source
    if event.location_name and event.start_datetime and event.source:
        duplicate = await find_cross_origin_duplicate(
            pool=pool,
            title=event.title,
            start_date=event.start_datetime.date(),
            location_name=event.location_name,
            exclude_source=event.source,
        )
        if duplicate:
            return "unchanged"  # Treat as no-op; existing event wins

    async with pool.acquire() as conn:
        result = await conn.execute(
            UPSERT_SQL,
            event.title,
            event.description,
            event.start_datetime,
            event.end_datetime,
            event.is_recurring,
            event.recurrence_pattern,
            event.recurrence_days_of_week,
            date.fromisoformat(event.recurrence_end_date)
            if isinstance(event.recurrence_end_date, str)
            else event.recurrence_end_date,
            event.recurrence_interval,
            event.location_name,
            event.address,
            event.city,
            float(event.latitude) if event.latitude else None,
            float(event.longitude) if event.longitude else None,
            event.timezone,
            event.category,
            event.age_min,
            event.age_max,
            event.cost_type,
            float(event.cost_amount) if event.cost_amount else None,
            event.cost_details,
            event.image_urls,
            event.source,
            event.source_url,
            event.source_fingerprint,
            event.registration_url,
            event.status,
            event.visibility,
            event.series_key,
        )

        # asyncpg returns "INSERT 0 1" for insert, "UPDATE 1" for update, "UPDATE 0" for no change
        if "INSERT" in result:
            return "inserted"
        elif result.endswith("1"):
            return "updated"
        else:
            return "unchanged"


async def upsert_batch(
    pool: asyncpg.Pool,
    events: list[NormalizedEvent],
    dry_run: bool = False,
) -> dict[str, int]:
    """Upsert a batch of events, returning counts by result type.

    Args:
        pool: asyncpg connection pool.
        events: List of normalized events to upsert.
        dry_run: If True, skip actual DB writes.

    Returns:
        Dict with counts: {'inserted': N, 'updated': N, 'unchanged': N, 'errors': N}
    """
    counts: dict[str, int] = {"inserted": 0, "updated": 0, "unchanged": 0, "errors": 0}

    for event in events:
        if dry_run:
            counts["inserted"] += 1  # Assume all would be inserted in dry run
            continue

        try:
            result = await upsert_event(pool, event)
            counts[result] += 1
        except Exception:
            logger.exception(
                "upsert_error",
                fingerprint=event.source_fingerprint,
                title=event.title,
            )
            counts["errors"] += 1

    return counts


async def hide_stale_events(
    pool: asyncpg.Pool,
    source_id: str,
    seen_fingerprints: list[str],
) -> int:
    """Hide auto-imported events that disappeared from the source.

    Events from the same source that weren't seen in the current scrape
    and are older than 48 hours get hidden. The grace period prevents
    false positives during temporary scrape failures.

    Args:
        pool: asyncpg connection pool.
        source_id: Source config ID prefix for fingerprint matching.
        seen_fingerprints: Fingerprints seen in the current scrape run.

    Returns:
        Number of events hidden.
    """
    async with pool.acquire() as conn:
        # Hide active auto-imported events whose fingerprints are not in the seen list
        # and haven't been updated in 48+ hours (grace period for transient scrape failures)
        result = await conn.execute(
            """
            UPDATE events
            SET status = 'hidden', updated_at = NOW()
            WHERE source = 'auto-imported'
              AND source_fingerprint IS NOT NULL
              AND source_fingerprint != ALL($1::text[])
              AND status = 'active'
              AND updated_at < NOW() - INTERVAL '48 hours';
            """,
            seen_fingerprints,
        )

        # Parse "UPDATE N" to get count
        hidden_count = int(result.split()[-1]) if result else 0
        if hidden_count > 0:
            logger.info("stale_events_hidden", source_id=source_id, count=hidden_count)
        return hidden_count
