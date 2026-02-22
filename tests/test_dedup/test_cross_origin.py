"""Tests for cross-origin duplicate detection."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from event_engine.dedup.cross_origin import find_cross_origin_duplicate


class TestCrossOriginDuplicate:
    @pytest.mark.asyncio
    async def test_exact_match_detected(self):
        """Identical title+date+location across sources is a duplicate."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_conn.fetchrow = AsyncMock(return_value={
            "id": "abc-123",
            "title": "Family Storytime",
            "source": "manual",
        })

        result = await find_cross_origin_duplicate(
            pool=mock_pool,
            title="Family Storytime",
            start_date="2026-03-15",
            location_name="Oberlin Regional Library",
            exclude_source="pipeline_library",
        )
        assert result is not None
        assert result["source"] == "manual"

    @pytest.mark.asyncio
    async def test_no_match_returns_none(self):
        """No existing event → returns None, safe to insert."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_conn.fetchrow = AsyncMock(return_value=None)

        result = await find_cross_origin_duplicate(
            pool=mock_pool,
            title="Unique Event Nobody Has",
            start_date="2026-03-15",
            location_name="Some Library",
            exclude_source="pipeline_library",
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_same_source_not_checked(self):
        """Cross-origin only — doesn't flag same-source events (fingerprint handles those)."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_conn.fetchrow = AsyncMock(return_value=None)

        await find_cross_origin_duplicate(
            pool=mock_pool,
            title="Family Storytime",
            start_date="2026-03-15",
            location_name="Oberlin Regional Library",
            exclude_source="pipeline_library",
        )
        # Verify the SQL excluded the same source
        call_args = mock_conn.fetchrow.call_args
        assert "pipeline_library" in str(call_args)
