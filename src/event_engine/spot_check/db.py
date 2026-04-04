"""Database helpers for spot_check: graduation audit and weekly spot-check."""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


async def fetch_random_pending_events(pool, domain: str, limit: int = 10) -> list[dict]:
    """Fetch random pending events whose source_url contains the given domain."""
    sql = """
        SELECT id, title, source_url
        FROM events
        WHERE source_url ILIKE $1
          AND status = 'pending'
        ORDER BY random()
        LIMIT $2
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, f"%{domain}%", limit)
    return [dict(r) for r in rows]


async def fetch_random_active_events(pool, domain: str, limit: int = 5) -> list[dict]:
    """Fetch random active events whose source_url contains the given domain."""
    sql = """
        SELECT id, title, source_url
        FROM events
        WHERE source_url ILIKE $1
          AND status = 'active'
        ORDER BY random()
        LIMIT $2
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, f"%{domain}%", limit)
    return [dict(r) for r in rows]


async def upsert_trust_override(pool, source_id: str, trust_level: str) -> None:
    """Write or update a trust override for a source."""
    sql = """
        INSERT INTO source_trust_overrides (source, trust_level)
        VALUES ($1, $2)
        ON CONFLICT (source) DO UPDATE SET trust_level = EXCLUDED.trust_level
    """
    async with pool.acquire() as conn:
        await conn.execute(sql, source_id, trust_level)
    logger.info("trust_override_written", source=source_id, trust_level=trust_level)


async def bulk_activate_pending_events(pool, domain: str) -> int:
    """Set status='active' on all pending events for a domain. Returns count updated."""
    sql = """
        UPDATE events
        SET status = 'active', updated_at = NOW()
        WHERE source_url ILIKE $1
          AND status = 'pending'
    """
    async with pool.acquire() as conn:
        result = await conn.execute(sql, f"%{domain}%")
    count = int(result.split()[-1]) if result else 0
    logger.info("bulk_activated", domain=domain, count=count)
    return count


async def fetch_verified_cvb_sources(pool) -> list[str]:
    """Return source IDs with trust_level='verified' in source_trust_overrides."""
    sql = "SELECT source FROM source_trust_overrides WHERE trust_level = 'verified'"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)
    return [row["source"] for row in rows]
