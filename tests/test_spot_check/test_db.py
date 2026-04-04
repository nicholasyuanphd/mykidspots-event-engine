"""Tests for spot_check DB helpers."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from event_engine.spot_check.db import (
    fetch_random_pending_events,
    fetch_random_active_events,
    upsert_trust_override,
    bulk_activate_pending_events,
    fetch_verified_cvb_sources,
)


@pytest.mark.asyncio
async def test_fetch_random_pending_events_queries_by_domain():
    """fetch_random_pending_events queries events by domain and status=pending."""
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"id": "abc", "title": "Kids Day", "source_url": "https://www.visitraleigh.com/events/kids-day"}
    ]
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    results = await fetch_random_pending_events(mock_pool, domain="www.visitraleigh.com", limit=10)

    assert len(results) == 1
    assert results[0]["title"] == "Kids Day"
    call_args = conn.fetch.call_args
    assert "www.visitraleigh.com" in str(call_args)
    assert "pending" in str(call_args)


@pytest.mark.asyncio
async def test_upsert_trust_override_calls_correct_sql():
    """upsert_trust_override writes source + trust_level to source_trust_overrides."""
    conn = AsyncMock()
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    await upsert_trust_override(mock_pool, source_id="visitraleigh-events", trust_level="verified")

    conn.execute.assert_called_once()
    sql, *args = conn.execute.call_args[0]
    assert "source_trust_overrides" in sql
    assert "ON CONFLICT" in sql
    assert "visitraleigh-events" in args
    assert "verified" in args


@pytest.mark.asyncio
async def test_bulk_activate_pending_events_returns_count():
    """bulk_activate_pending_events returns number of rows updated."""
    conn = AsyncMock()
    conn.execute.return_value = "UPDATE 15"
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    count = await bulk_activate_pending_events(mock_pool, domain="www.visitraleigh.com")

    assert count == 15


@pytest.mark.asyncio
async def test_fetch_verified_cvb_sources_filters_by_trust_level():
    """fetch_verified_cvb_sources returns only sources with trust_level=verified."""
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"source": "visitraleigh-events"},
        {"source": "wilmington-beaches-events"},
    ]
    mock_pool = MagicMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    result = await fetch_verified_cvb_sources(mock_pool)

    assert result == ["visitraleigh-events", "wilmington-beaches-events"]
    call_args = conn.fetch.call_args
    assert "verified" in str(call_args)
