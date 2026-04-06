"""Tests for import_log silent failure detection."""
import pytest
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

from event_engine.db.import_log import fetch_silent_failures


def _make_pool(rows: list[dict]) -> MagicMock:
    """Return a mock pool whose conn.fetch returns the given rows."""
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    # asyncpg rows are dict-like; we return plain dicts and the code does dict(r)
    mock_conn.fetch = AsyncMock(return_value=rows)
    return mock_pool


class TestFetchSilentFailures:
    @pytest.mark.asyncio
    async def test_returns_silent_failures(self):
        """Sources with 0 events this run but historical peak ≥10 are returned."""
        now = datetime.now(UTC)
        rows = [
            {
                "source_id": "visitraleigh-events",
                "source_name": "Visit Raleigh Events",
                "latest_run": now,
                "peak_events_found": 142,
            },
        ]
        pool = _make_pool(rows)

        result = await fetch_silent_failures(pool, since_hours=2, min_historical_events=10)

        assert len(result) == 1
        assert result[0]["source_id"] == "visitraleigh-events"
        assert result[0]["peak_events_found"] == 142

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_failures(self):
        """No silent failures → empty list (all sources returned events)."""
        pool = _make_pool([])

        result = await fetch_silent_failures(pool, since_hours=2, min_historical_events=10)

        assert result == []

    @pytest.mark.asyncio
    async def test_multiple_failures_ordered_by_peak(self):
        """Multiple failures are returned in descending peak_events_found order."""
        now = datetime.now(UTC)
        rows = [
            {"source_id": "source-a", "source_name": "A", "latest_run": now, "peak_events_found": 300},
            {"source_id": "source-b", "source_name": "B", "latest_run": now, "peak_events_found": 50},
        ]
        pool = _make_pool(rows)

        result = await fetch_silent_failures(pool)

        # ORDER BY peak_events_found DESC is enforced in SQL; mock returns in that order
        assert result[0]["peak_events_found"] == 300
        assert result[1]["peak_events_found"] == 50

    @pytest.mark.asyncio
    async def test_passes_parameters_to_query(self):
        """since_hours and min_historical_events are forwarded to the SQL query."""
        pool = _make_pool([])

        await fetch_silent_failures(pool, since_hours=6, min_historical_events=20)

        mock_conn = pool.acquire.return_value.__aenter__.return_value
        call_args = mock_conn.fetch.call_args
        # First positional arg is the SQL string; $1=since_hours, $2=min_historical_events
        assert call_args.args[1] == 6
        assert call_args.args[2] == 20
