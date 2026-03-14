"""Tests for the Drupal FullCalendar scraper."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from event_engine.models import SourceConfig
from event_engine.models.source_config import LocationConfig
from event_engine.scrapers.drupal_fullcalendar import DrupalFullCalendarScraper

FIXTURE_DIR = Path(__file__).parent.parent / "fixtures"
INLINE_HTML = (FIXTURE_DIR / "drupal_fullcalendar_inline.html").read_text()
FEED_JSON = json.loads((FIXTURE_DIR / "drupal_fullcalendar_feed.json").read_text())

KNIGHTDALE_CONFIG = SourceConfig(
    id="knightdale-town-calendar",
    name="Town of Knightdale",
    platform="drupal_fullcalendar",
    trust_level="new",
    content_policy="government",
    enabled=True,
    timezone="America/New_York",
    base_url="https://www.knightdalenc.gov/calendar",
    location_id="",
    location=LocationConfig(
        name="Town of Knightdale",
        address="950 Steeple Square Ct, Knightdale, NC 27545",
        city="Knightdale",
        latitude="35.7874",
        longitude="-78.4906",
    ),
    default_cost_type="free",
    category_overrides=["community"],
    request_delay_ms=0,
    selectors={"mode": "inline"},
)

WAKE_FOREST_CONFIG = SourceConfig(
    id="wake-forest-town-calendar",
    name="Town of Wake Forest",
    platform="drupal_fullcalendar",
    trust_level="new",
    content_policy="government",
    enabled=True,
    timezone="America/New_York",
    base_url="https://www.wakeforestnc.gov",
    location_id="",
    location=LocationConfig(
        name="Town of Wake Forest",
        address="301 S Brooks St, Wake Forest, NC 27587",
        city="Wake Forest",
        latitude="35.9799",
        longitude="-78.5097",
    ),
    default_cost_type="free",
    category_overrides=["community"],
    request_delay_ms=0,
    selectors={
        "mode": "feed",
        "feed_url": "/vc3-fullcalendar-events-feed",
        "feed_params": "id=events_calendar&display=all_events",
    },
)


def _make_inline_scraper(html: str) -> DrupalFullCalendarScraper:
    """Create scraper with mocked fetch returning HTML."""
    client = MagicMock(spec=httpx.AsyncClient)
    scraper = DrupalFullCalendarScraper(KNIGHTDALE_CONFIG, client)
    scraper.fetch = AsyncMock(return_value=MagicMock(text=html, status_code=200))  # type: ignore[method-assign]
    return scraper


def _make_feed_scraper(json_data: list) -> DrupalFullCalendarScraper:
    """Create scraper with mocked fetch_json returning JSON data."""
    client = MagicMock(spec=httpx.AsyncClient)
    scraper = DrupalFullCalendarScraper(WAKE_FOREST_CONFIG, client)
    scraper.fetch_json = AsyncMock(return_value=json_data)  # type: ignore[method-assign]
    return scraper


class TestInlineMode:
    @pytest.mark.asyncio
    async def test_scrapes_events_from_inline_html(self) -> None:
        """Should extract events from drupalSettings JSON embedded in HTML."""
        scraper = _make_inline_scraper(INLINE_HTML)
        events = await scraper.scrape_all()
        # 5 events in fixture, but one has empty title → 4 valid
        assert len(events) == 4

    @pytest.mark.asyncio
    async def test_event_fields_populated(self) -> None:
        """Each event should have title, external_id, raw_start, and source_url."""
        scraper = _make_inline_scraper(INLINE_HTML)
        events = await scraper.scrape_all()
        for event in events:
            assert event.title
            assert event.external_id
            assert event.raw_start
            assert event.source_url.startswith("https://www.knightdalenc.gov/event/")

    @pytest.mark.asyncio
    async def test_all_day_event_parsed(self) -> None:
        """All-day events should have start but no end time."""
        scraper = _make_inline_scraper(INLINE_HTML)
        events = await scraper.scrape_all()
        all_day = next((e for e in events if "Independence" in e.title), None)
        assert all_day is not None
        assert all_day.raw_start == "2026-07-04"
        assert all_day.raw_end == ""

    @pytest.mark.asyncio
    async def test_empty_title_skipped(self) -> None:
        """Events with empty titles should be skipped."""
        scraper = _make_inline_scraper(INLINE_HTML)
        events = await scraper.scrape_all()
        eids = [e.external_id for e in events]
        assert "500-D-0" not in eids

    @pytest.mark.asyncio
    async def test_no_drupal_settings_returns_empty(self) -> None:
        """Page without drupalSettings should return no events."""
        scraper = _make_inline_scraper("<html><body>No calendar here</body></html>")
        events = await scraper.scrape_all()
        assert events == []


class TestFeedMode:
    @pytest.mark.asyncio
    async def test_scrapes_events_from_feed(self) -> None:
        """Should parse events from JSON feed, deduplicating by ID."""
        scraper = _make_feed_scraper(FEED_JSON)
        events = await scraper.scrape_all()
        # 5 items in fixture: 2 dupes of 7700, 1 valid (9190), 1 valid (8001),
        # 1 empty id → skipped, so 3 unique
        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_feed_event_fields(self) -> None:
        """Feed events should have proper source_url and times."""
        scraper = _make_feed_scraper(FEED_JSON)
        events = await scraper.scrape_all()
        market = next((e for e in events if "Farmers" in e.title), None)
        assert market is not None
        assert market.source_url == "https://www.wakeforestnc.gov/event/farmers-market"
        assert "2026-03-14" in market.raw_start

    @pytest.mark.asyncio
    async def test_deduplicates_recurring_instances(self) -> None:
        """Same event ID appearing multiple times should be deduplicated."""
        scraper = _make_feed_scraper(FEED_JSON)
        events = await scraper.scrape_all()
        ids = [e.external_id for e in events]
        assert len(ids) == len(set(ids))

    @pytest.mark.asyncio
    async def test_empty_feed_returns_empty(self) -> None:
        """Empty JSON array should return no events."""
        scraper = _make_feed_scraper([])
        events = await scraper.scrape_all()
        assert events == []

    @pytest.mark.asyncio
    async def test_non_list_response_returns_empty(self) -> None:
        """Non-list JSON response should return no events."""
        scraper = _make_feed_scraper({"error": "not found"})  # type: ignore[arg-type]
        events = await scraper.scrape_all()
        assert events == []

    @pytest.mark.asyncio
    async def test_empty_id_skipped(self) -> None:
        """Events with empty/missing ID should be skipped."""
        scraper = _make_feed_scraper(FEED_JSON)
        events = await scraper.scrape_all()
        titles = [e.title for e in events]
        assert "Event With No ID" not in titles
