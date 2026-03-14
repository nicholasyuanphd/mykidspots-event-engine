"""Tests for the Revize CMS calendar scraper."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from event_engine.models import SourceConfig
from event_engine.models.source_config import LocationConfig
from event_engine.scrapers.revize import RevizeScraper

FIXTURE_JSON = json.loads(
    (Path(__file__).parent.parent / "fixtures" / "revize_calendar.json").read_text()
)

WENDELL_CONFIG = SourceConfig(
    id="wendell-town-calendar",
    name="Town of Wendell",
    platform="revize",
    trust_level="new",
    content_policy="government",
    enabled=True,  # enabled for testing even though real source is blocked
    timezone="America/New_York",
    base_url="https://townofwendellnc.gov",
    location_id="1,2,3,6",
    location=LocationConfig(
        name="Town of Wendell",
        address="15 E Fourth St, Wendell, NC 27591",
        city="Wendell",
        latitude="35.7813",
        longitude="-78.3697",
    ),
    default_cost_type="free",
    category_overrides=["community"],
    request_delay_ms=0,
)


def _make_scraper(json_data: list) -> RevizeScraper:
    """Create a RevizeScraper with mocked fetch_json."""
    client = MagicMock(spec=httpx.AsyncClient)
    scraper = RevizeScraper(WENDELL_CONFIG, client)
    scraper.fetch_json = AsyncMock(return_value=json_data)  # type: ignore[method-assign]
    return scraper


class TestRevizeScraper:
    @pytest.mark.asyncio
    async def test_scrapes_events(self) -> None:
        """Should parse valid events from Revize JSON, skipping invalid ones."""
        scraper = _make_scraper(FIXTURE_JSON)
        events = await scraper.scrape_all()
        # 6 items: id=101 appears twice (dedup), id="" skipped, title="" skipped
        # Valid unique: 101, 102, 103 = 3
        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_event_fields(self) -> None:
        """Events should have proper fields populated."""
        scraper = _make_scraper(FIXTURE_JSON)
        events = await scraper.scrape_all()
        board = next((e for e in events if "Board" in e.title), None)
        assert board is not None
        assert board.external_id == "101"
        assert board.source_url == "https://townofwendellnc.gov/government/agendas___minutes.php?id=101"
        assert board.description == "Regular monthly board meeting"
        assert "2026-03-10" in board.raw_start

    @pytest.mark.asyncio
    async def test_deduplicates_by_id(self) -> None:
        """Same event ID should be deduplicated."""
        scraper = _make_scraper(FIXTURE_JSON)
        events = await scraper.scrape_all()
        ids = [e.external_id for e in events]
        assert len(ids) == len(set(ids))

    @pytest.mark.asyncio
    async def test_empty_id_skipped(self) -> None:
        """Events with empty ID should be skipped."""
        scraper = _make_scraper(FIXTURE_JSON)
        events = await scraper.scrape_all()
        titles = [e.title for e in events]
        assert "Bad Event No ID" not in titles

    @pytest.mark.asyncio
    async def test_empty_title_skipped(self) -> None:
        """Events with empty title should be skipped."""
        scraper = _make_scraper(FIXTURE_JSON)
        events = await scraper.scrape_all()
        ids = [e.external_id for e in events]
        assert "105" not in ids

    @pytest.mark.asyncio
    async def test_empty_response(self) -> None:
        """Empty JSON array should return no events."""
        scraper = _make_scraper([])
        events = await scraper.scrape_all()
        assert events == []

    @pytest.mark.asyncio
    async def test_fetch_failure_returns_empty(self) -> None:
        """Fetch failure should return empty list, not crash."""
        client = MagicMock(spec=httpx.AsyncClient)
        scraper = RevizeScraper(WENDELL_CONFIG, client)
        scraper.fetch_json = AsyncMock(side_effect=Exception("connection refused"))  # type: ignore[method-assign]
        events = await scraper.scrape_all()
        assert events == []

    @pytest.mark.asyncio
    async def test_event_without_url(self) -> None:
        """Events without URL should have empty source_url."""
        scraper = _make_scraper(FIXTURE_JSON)
        events = await scraper.scrape_all()
        soccer = next((e for e in events if "Soccer" in e.title), None)
        assert soccer is not None
        assert soccer.source_url == ""

    @pytest.mark.asyncio
    async def test_all_day_event(self) -> None:
        """All-day events should be parsed correctly."""
        scraper = _make_scraper(FIXTURE_JSON)
        events = await scraper.scrape_all()
        soccer = next((e for e in events if "Soccer" in e.title), None)
        assert soccer is not None
        assert "2026-03-15" in soccer.raw_start
