"""Tests for DB hygiene scan."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from event_engine.db.hygiene import run_hygiene_scan


class TestHygieneScan:
    @pytest.mark.asyncio
    async def test_returns_hygiene_report(self):
        """Hygiene scan returns counts of issues found."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_conn.fetchval = AsyncMock(side_effect=[5, 3])  # 5 manual dupes, 3 past-date

        report = await run_hygiene_scan(pool=mock_pool)

        assert report["manual_duplicates"] == 5
        assert report["past_date_active"] == 3

    @pytest.mark.asyncio
    async def test_zero_issues_clean_db(self):
        """Clean DB returns all zeros."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_conn.fetchval = AsyncMock(side_effect=[0, 0])

        report = await run_hygiene_scan(pool=mock_pool)

        assert report["manual_duplicates"] == 0
        assert report["past_date_active"] == 0
