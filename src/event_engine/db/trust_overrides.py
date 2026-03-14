"""Fetch source trust overrides from the database.

The curator console (mykidspots app) writes trust overrides to the
``source_trust_overrides`` table. This module reads those overrides so the
engine can honour curator decisions at scrape time, bypassing the YAML
``trust_level`` default when an override exists.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from event_engine.models.source_config import TrustLevel

logger = structlog.get_logger()


async def fetch_trust_overrides(pool) -> dict[str, TrustLevel]:
    """Query source_trust_overrides and return a mapping of source ID → trust level.

    Args:
        pool: asyncpg connection pool.

    Returns:
        Dict mapping source ID (str) to TrustLevel ('verified' or 'new').
        Returns an empty dict if the table does not exist, is empty, or the
        query fails for any reason — the scrape must never be blocked by this
        call.
    """
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT source, trust_level FROM source_trust_overrides"
            )
        overrides: dict[str, TrustLevel] = {}
        for row in rows:
            source_id: str = row["source"]
            trust_level: str = row["trust_level"]
            if trust_level in ("verified", "new"):
                overrides[source_id] = trust_level  # type: ignore[assignment]
            else:
                logger.warning(
                    "trust_override_unknown_value",
                    source=source_id,
                    trust_level=trust_level,
                )
        logger.info("trust_overrides_loaded", count=len(overrides))
        return overrides
    except Exception:
        logger.warning(
            "trust_overrides_fetch_failed",
            exc_info=True,
            note="Falling back to YAML trust_level for all sources",
        )
        return {}
