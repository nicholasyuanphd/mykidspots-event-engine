"""Tests for the CivicPlus HTML list-view scraper."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from event_engine.models import SourceConfig
from event_engine.models.source_config import LocationConfig
from event_engine.scrapers.civicplus import CivicPlusScraper

FIXTURE_HTML = (Path(__file__).parent.parent / "fixtures" / "civicplus_list_page.html").read_text()

EMPTY_HTML = """<!DOCTYPE html>
<html>
<body>
<ul class="list-group list-group-flush">
</ul>
</body>
</html>"""

HOLLY_SPRINGS_CONFIG = SourceConfig(
    id="holly-springs-town",
    name="Town of Holly Springs",
    platform="civicplus",
    trust_level="new",
    content_policy="government",
    enabled=True,
    timezone="America/New_York",
    base_url="https://www.hollyspringsnc.gov/calendar.aspx",
    location_id="0",
    location=LocationConfig(
        name="Holly Springs",
        city="Holly Springs",
        latitude="35.6499",
        longitude="-78.8336",
    ),
    default_cost_type="free",
    category_overrides=["community"],
    request_delay_ms=0,
)


def _make_scraper(source: SourceConfig, html: str) -> CivicPlusScraper:
    """Create a CivicPlusScraper with fetch mocked to always return the given HTML."""
    client = MagicMock(spec=httpx.AsyncClient)
    scraper = CivicPlusScraper(source, client)
    scraper.fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=MagicMock(text=html, status_code=200),
    )
    return scraper


class TestCivicPlusScraper:
    @pytest.mark.asyncio
    async def test_scrapes_events_from_list_view(self) -> None:
        """Should return 3 unique events from fixture HTML across 3 deduplicated windows."""
        scraper = _make_scraper(HOLLY_SPRINGS_CONFIG, FIXTURE_HTML)
        events = await scraper.scrape_all()
        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_event_has_required_fields(self) -> None:
        """Each event must have a non-empty title, external_id, and raw_start."""
        scraper = _make_scraper(HOLLY_SPRINGS_CONFIG, FIXTURE_HTML)
        events = await scraper.scrape_all()

        for event in events:
            assert event.title, f"Event missing title: {event}"
            assert event.external_id, f"Event missing external_id: {event}"
            assert event.raw_start, f"Event missing raw_start: {event}"

    @pytest.mark.asyncio
    async def test_deduplicates_across_date_windows(self) -> None:
        """Events from 3 overlapping date windows should not be duplicated."""
        scraper = _make_scraper(HOLLY_SPRINGS_CONFIG, FIXTURE_HTML)
        events = await scraper.scrape_all()

        external_ids = [e.external_id for e in events]
        assert len(external_ids) == len(set(external_ids)), "Duplicate external_ids found"

    @pytest.mark.asyncio
    async def test_all_day_event_does_not_crash(self) -> None:
        """An all-day event (no time component) should return the date as raw_start."""
        scraper = _make_scraper(HOLLY_SPRINGS_CONFIG, FIXTURE_HTML)
        events = await scraper.scrape_all()

        # EID=999 is "Spring Break Week" with date only: "March 16, 2026"
        all_day = next((e for e in events if e.external_id == "999"), None)
        assert all_day is not None, "All-day event (EID=999) not found"
        assert "March 16, 2026" in all_day.raw_start
        assert all_day.raw_end == ""

    @pytest.mark.asyncio
    async def test_empty_response_returns_no_events(self) -> None:
        """Empty <ul> should produce an empty event list."""
        scraper = _make_scraper(HOLLY_SPRINGS_CONFIG, EMPTY_HTML)
        events = await scraper.scrape_all()
        assert events == []

    @pytest.mark.asyncio
    async def test_window_fetch_failure_is_isolated(self) -> None:
        """One failing window should not abort the entire scrape."""
        client = MagicMock(spec=httpx.AsyncClient)
        scraper = CivicPlusScraper(HOLLY_SPRINGS_CONFIG, client)
        good_response = MagicMock(text=FIXTURE_HTML, status_code=200)
        # First window fails, second and third succeed
        scraper.fetch = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                Exception("connection timeout"),
                good_response,
                good_response,
            ]
        )
        events = await scraper.scrape_all()
        # Should still get events from the 2 successful windows
        # (dedup means same 3 events from fixture, not 6)
        assert len(events) == 3
