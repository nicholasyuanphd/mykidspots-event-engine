"""Tests for the Meilisearch tourism scraper (Charlotte's Got A Lot)."""
import pytest
from unittest.mock import AsyncMock, MagicMock
import httpx

from event_engine.models import SourceConfig
from event_engine.models.source_config import LocationConfig
from event_engine.scrapers.meilisearch_tourism import MeilisearchTourismScraper


CHARLOTTE_CONFIG = SourceConfig(
    id="charlottesgotalot-events",
    name="Charlotte's Got A Lot",
    platform="meilisearch",
    trust_level="new",
    content_policy="commercial",
    enabled=True,
    timezone="America/New_York",
    base_url="https://www.charlottesgotalot.com",
    location_id="",
    location=LocationConfig(
        name="Charlotte",
        city="Charlotte",
        latitude="35.2271",
        longitude="-80.8431",
    ),
    ai_classification="required",
    request_delay_ms=0,
    selectors={
        "search_url": "https://search.charlottesgotalot.com",
        "index": "cluster_events",
        "api_key": "GKEuAXwR4*em@MJ7",
    },
)

# Unix timestamps for April 2026
SAMPLE_HIT = {
    "id": "charlotte-event-001",
    "title": "Charlotte Family Festival",
    "startDate": 1775707200,   # 2026-04-09T00:00:00 UTC
    "endDate": 1775750400,     # 2026-04-09T12:00:00 UTC
    "description": "Annual family festival in Charlotte.",
    "fullAddress": "100 E Trade St, Charlotte, NC 28202",
    "city": "Charlotte",
    "state": "NC",
    "postalCode": "28202",
    "latitude": 35.2271,
    "longitude": -80.8431,
    "website": "https://www.charlottesgotalot.com/events/family-festival",
    "uri": "events/family-festival",
    "isFree": True,
    "eventPrice": 0,
    "place": {"title": "Uptown Charlotte"},
    "displayDate": "April 9, 2026",
}

SAMPLE_RESPONSE = {
    "hits": [SAMPLE_HIT],
    "estimatedTotalHits": 1,
    "limit": 50,
    "offset": 0,
}

EMPTY_RESPONSE = {
    "hits": [],
    "estimatedTotalHits": 0,
    "limit": 50,
    "offset": 0,
}


def _make_scraper(source: SourceConfig, api_responses: list[dict]) -> MeilisearchTourismScraper:
    client = MagicMock(spec=httpx.AsyncClient)
    scraper = MeilisearchTourismScraper(source, client)
    call_iter = iter(api_responses)

    async def mock_fetch_json(url: str, **kwargs) -> dict:
        return next(call_iter)

    scraper.fetch_json = AsyncMock(side_effect=mock_fetch_json)
    return scraper


class TestMeilisearchTourismScraper:
    @pytest.mark.asyncio
    async def test_scrapes_events(self) -> None:
        """Scraper returns events from Meilisearch response."""
        scraper = _make_scraper(CHARLOTTE_CONFIG, [SAMPLE_RESPONSE])
        events = await scraper.scrape_all()
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_event_has_required_fields(self) -> None:
        """Events have title, external_id, and raw_start set."""
        scraper = _make_scraper(CHARLOTTE_CONFIG, [SAMPLE_RESPONSE])
        events = await scraper.scrape_all()
        event = events[0]
        assert event.title == "Charlotte Family Festival"
        assert event.external_id == "charlotte-event-001"
        # raw_start should be an ISO datetime string converted from Unix timestamp
        assert "2026-04-09" in event.raw_start

    @pytest.mark.asyncio
    async def test_event_source_url_built_from_uri(self) -> None:
        """source_url is built from base_url + uri field."""
        scraper = _make_scraper(CHARLOTTE_CONFIG, [SAMPLE_RESPONSE])
        events = await scraper.scrape_all()
        assert "charlottesgotalot.com" in events[0].source_url
        assert "events/family-festival" in events[0].source_url

    @pytest.mark.asyncio
    async def test_event_geo_coordinates_set(self) -> None:
        """latitude and longitude are stored in raw_data for normalizer."""
        scraper = _make_scraper(CHARLOTTE_CONFIG, [SAMPLE_RESPONSE])
        events = await scraper.scrape_all()
        event = events[0]
        assert event.raw_data.get("latitude") == 35.2271
        assert event.raw_data.get("longitude") == -80.8431

    @pytest.mark.asyncio
    async def test_empty_response_yields_no_events(self) -> None:
        """Empty Meilisearch response yields zero events."""
        scraper = _make_scraper(CHARLOTTE_CONFIG, [EMPTY_RESPONSE])
        events = await scraper.scrape_all()
        assert events == []

    @pytest.mark.asyncio
    async def test_source_id_set_on_events(self) -> None:
        """Events carry source_id from config."""
        scraper = _make_scraper(CHARLOTTE_CONFIG, [SAMPLE_RESPONSE])
        events = await scraper.scrape_all()
        assert all(e.source_id == "charlottesgotalot-events" for e in events)

    @pytest.mark.asyncio
    async def test_deduplicates_events_across_pages(self) -> None:
        """Same event appearing in multiple paginated responses is deduplicated."""
        scraper = _make_scraper(CHARLOTTE_CONFIG, [SAMPLE_RESPONSE, SAMPLE_RESPONSE])
        events = await scraper.scrape_all()
        ids = [e.external_id for e in events]
        assert len(ids) == len(set(ids))

    @pytest.mark.asyncio
    async def test_unix_timestamp_converted_to_iso(self) -> None:
        """Unix second timestamps are converted to ISO datetime strings."""
        scraper = _make_scraper(CHARLOTTE_CONFIG, [SAMPLE_RESPONSE])
        events = await scraper.scrape_all()
        # 1775707200 seconds = 2026-04-09T00:00:00 UTC
        assert "2026" in events[0].raw_start
        assert "T" in events[0].raw_start  # ISO format has T separator
