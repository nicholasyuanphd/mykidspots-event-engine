"""Tests for the WakeGov scraper."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from event_engine.models import SourceConfig
from event_engine.models.source_config import LocationConfig
from event_engine.scrapers.wakegov import WakeGovScraper

FIXTURE_HTML = (Path(__file__).parent.parent / "fixtures" / "wakegov_listing.html").read_text()


@pytest.fixture
def wakegov_source() -> SourceConfig:
    return SourceConfig(
        id="wake-oberlin-library",
        name="Oberlin Regional Library",
        platform="wakegov",
        trust_level="verified",
        timezone="America/New_York",
        base_url="https://www.wake.gov/events",
        location_id="396",
        location=LocationConfig(
            name="Oberlin Regional Library",
            address="1101 Oberlin Rd, Raleigh, NC 27605",
            city="Raleigh",
        ),
        request_delay_ms=0,  # No delay in tests
    )


def _make_scraper(source: SourceConfig) -> WakeGovScraper:
    """Create a WakeGovScraper with a mocked fetch method."""
    client = MagicMock(spec=httpx.AsyncClient)
    scraper = WakeGovScraper(source, client)
    scraper.fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=MagicMock(text=FIXTURE_HTML, status_code=200),
    )
    return scraper


class TestWakeGovScraper:
    @pytest.mark.asyncio
    async def test_scrape_parses_events(self, wakegov_source: SourceConfig) -> None:
        scraper = _make_scraper(wakegov_source)
        events = await scraper.scrape_all()
        assert len(events) == 3

    @pytest.mark.asyncio
    async def test_event_titles_extracted(self, wakegov_source: SourceConfig) -> None:
        scraper = _make_scraper(wakegov_source)
        events = await scraper.scrape_all()
        titles = [e.title for e in events]
        assert "Baby Playdate" in titles
        assert "Family Storytime" in titles
        assert "Teen Game Night" in titles

    @pytest.mark.asyncio
    async def test_event_external_ids(self, wakegov_source: SourceConfig) -> None:
        scraper = _make_scraper(wakegov_source)
        events = await scraper.scrape_all()
        ids = [e.external_id for e in events]
        assert "baby-playdate-31" in ids
        assert "family-storytime-2453" in ids
        assert "teen-game-night-5" in ids

    @pytest.mark.asyncio
    async def test_recurring_event_detected(self, wakegov_source: SourceConfig) -> None:
        scraper = _make_scraper(wakegov_source)
        events = await scraper.scrape_all()
        event_map = {e.external_id: e for e in events}

        # Family Storytime has a date range → recurring
        assert event_map["family-storytime-2453"].is_recurring is True
        # Baby Playdate has a single date → not recurring
        assert event_map["baby-playdate-31"].is_recurring is False

    @pytest.mark.asyncio
    async def test_source_url_constructed(self, wakegov_source: SourceConfig) -> None:
        scraper = _make_scraper(wakegov_source)
        events = await scraper.scrape_all()
        event = next(e for e in events if e.external_id == "baby-playdate-31")
        assert event.source_url == ("https://www.wake.gov/events/baby-playdate-31")

    @pytest.mark.asyncio
    async def test_date_time_parsed(self, wakegov_source: SourceConfig) -> None:
        scraper = _make_scraper(wakegov_source)
        events = await scraper.scrape_all()
        event = next(e for e in events if e.external_id == "baby-playdate-31")
        assert "February 20, 2026" in event.raw_start
        assert "11:00 am" in event.raw_start

    @pytest.mark.asyncio
    async def test_missing_location_handled(self, wakegov_source: SourceConfig) -> None:
        scraper = _make_scraper(wakegov_source)
        events = await scraper.scrape_all()
        # Teen Game Night has no location element in the fixture
        event = next(e for e in events if e.external_id == "teen-game-night-5")
        assert event.raw_location == ""  # Gracefully empty

    @pytest.mark.asyncio
    async def test_scraper_uses_department_id_from_config(self) -> None:
        """WakeGovScraper uses department_id from SourceConfig when set."""
        config = SourceConfig(
            id="test-parks",
            name="Test Parks",
            platform="wakegov",
            base_url="https://www.wake.gov/events",
            location_id="",
            department_id="195",
            location=LocationConfig(name="Test", city="Raleigh"),
            request_delay_ms=0,
        )
        scraper = _make_scraper(config)
        # Trigger one scrape so fetch is called; capture the URL from the call args
        await scraper.scrape_all()
        called_url: str = scraper.fetch.call_args[0][0]  # type: ignore[union-attr]
        assert "field_department_target_id=195" in called_url
        assert "field_department_target_id=25" not in called_url
