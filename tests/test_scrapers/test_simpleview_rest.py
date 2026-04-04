"""Tests for the Simpleview REST scraper (v1 and v2 variants)."""
import pytest
from unittest.mock import AsyncMock, MagicMock
import httpx

from event_engine.models import SourceConfig
from event_engine.models.source_config import LocationConfig
from event_engine.scrapers.simpleview_rest import SimplyviewRestScraper

# --- Shared configs ---

def _make_config(rest_version: str, source_id: str, base_url: str) -> SourceConfig:
    return SourceConfig(
        id=source_id,
        name=f"Test {source_id}",
        platform="simpleview_rest",
        trust_level="new",
        content_policy="commercial",
        enabled=True,
        timezone="America/New_York",
        base_url=base_url,
        location_id="",
        location=LocationConfig(
            name="Test City",
            city="Raleigh",
            latitude="35.7796",
            longitude="-78.6382",
        ),
        ai_classification="required",
        request_delay_ms=0,
        selectors={"rest_version": rest_version},
    )

RALEIGH_CONFIG = _make_config("v1", "visit-raleigh", "https://www.visitraleigh.com")
WILMINGTON_CONFIG = _make_config("v2", "wilmington-beaches", "https://www.wilmingtonandbeaches.com")

# --- Sample API responses ---

SAMPLE_V1_RESPONSE = {
    "docs": [
        {
            "_id": "abc123",
            "title": "Spring Family Festival",
            "startDate": "2026-04-15T10:00:00",
            "endDate": "2026-04-15T16:00:00",
            "address1": "123 Main St",
            "city": "Raleigh",
            "state": "NC",
            "zip": "27601",
            "location": "Moore Square",
            "linkUrl": "https://www.visitraleigh.com/events/spring-festival",
            "description": "A fun festival for the whole family.",
            "categories": [{"catName": "Family Fun"}, {"catName": "Festivals"}],
        }
    ],
    "total": 1,
}

SAMPLE_V2_RESPONSE = {
    "docs": [
        {
            "_id": "def456",
            "title": "Beach Kite Festival",
            "startDate": "2026-04-20T09:00:00",
            "endDate": "2026-04-20T17:00:00",
            "address1": "100 N Lumina Ave",
            "city": "Wilmington",
            "state": "NC",
            "zip": "28480",
            "location": "Wrightsville Beach",
            "linkUrl": "https://www.wilmingtonandbeaches.com/events/kite-festival",
            "description": "Annual kite festival at the beach.",
            "categories": [{"catName": "Festivals"}, {"catName": "Family"}],
        }
    ],
    "total": 1,
}

EMPTY_RESPONSE = {"docs": [], "total": 0}


def _make_scraper(
    source: SourceConfig,
    api_responses: list[dict],
    token: str = "test-token-123",
) -> SimplyviewRestScraper:
    """Create a SimplyviewRestScraper with HTTP mocked.

    api_responses: list of dicts returned by fetch_json in sequence.
    First call is the token fetch (returns token string), rest are event pages.
    """
    client = MagicMock(spec=httpx.AsyncClient)
    scraper = SimplyviewRestScraper(source, client)

    # Mock fetch for token endpoint (returns plain text)
    token_response = MagicMock()
    token_response.text = token

    # Mock fetch_json for event pages
    fetch_json_calls = iter(api_responses)

    async def mock_fetch(url: str, **kwargs) -> MagicMock:
        return token_response

    async def mock_fetch_json(url: str, **kwargs) -> dict:
        return next(fetch_json_calls)

    scraper.fetch = AsyncMock(side_effect=mock_fetch)
    scraper.fetch_json = AsyncMock(side_effect=mock_fetch_json)
    return scraper


class TestSimplyviewRestScraper:
    @pytest.mark.asyncio
    async def test_v1_scrapes_events(self) -> None:
        """v1 config returns events from the Simpleview REST v1 API."""
        scraper = _make_scraper(RALEIGH_CONFIG, [SAMPLE_V1_RESPONSE])
        events = await scraper.scrape_all()
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_v2_scrapes_events(self) -> None:
        """v2 config returns events from the Simpleview REST v2 API."""
        scraper = _make_scraper(WILMINGTON_CONFIG, [SAMPLE_V2_RESPONSE])
        events = await scraper.scrape_all()
        assert len(events) == 1

    @pytest.mark.asyncio
    async def test_event_has_required_fields(self) -> None:
        """Events have title, external_id, and raw_start set."""
        scraper = _make_scraper(RALEIGH_CONFIG, [SAMPLE_V1_RESPONSE])
        events = await scraper.scrape_all()
        event = events[0]
        assert event.title == "Spring Family Festival"
        assert event.external_id == "abc123"
        assert event.raw_start == "2026-04-15T10:00:00"

    @pytest.mark.asyncio
    async def test_event_location_fields(self) -> None:
        """Events have location and address fields from API."""
        scraper = _make_scraper(RALEIGH_CONFIG, [SAMPLE_V1_RESPONSE])
        events = await scraper.scrape_all()
        event = events[0]
        assert event.raw_location == "Moore Square"
        assert "Main St" in event.raw_address

    @pytest.mark.asyncio
    async def test_event_source_url(self) -> None:
        """Events carry source_url from linkUrl field."""
        scraper = _make_scraper(RALEIGH_CONFIG, [SAMPLE_V1_RESPONSE])
        events = await scraper.scrape_all()
        assert "visitraleigh.com" in events[0].source_url

    @pytest.mark.asyncio
    async def test_empty_response_yields_no_events(self) -> None:
        """Empty API response yields zero events."""
        scraper = _make_scraper(RALEIGH_CONFIG, [EMPTY_RESPONSE])
        events = await scraper.scrape_all()
        assert events == []

    @pytest.mark.asyncio
    async def test_source_id_set_on_events(self) -> None:
        """Events carry source_id from config."""
        scraper = _make_scraper(RALEIGH_CONFIG, [SAMPLE_V1_RESPONSE])
        events = await scraper.scrape_all()
        assert all(e.source_id == "visit-raleigh" for e in events)

    @pytest.mark.asyncio
    async def test_deduplicates_events_across_pages(self) -> None:
        """Same event appearing in all 3 date windows is deduplicated to one result."""
        # Supply the same event response for each of the 3 windows
        scraper = _make_scraper(
            RALEIGH_CONFIG,
            [SAMPLE_V1_RESPONSE, SAMPLE_V1_RESPONSE, SAMPLE_V1_RESPONSE],
        )
        events = await scraper.scrape_all()
        # The same event (_id=abc123) appears in all 3 windows but should only appear once
        ids = [e.external_id for e in events]
        assert len(ids) == 1
        assert len(set(ids)) == 1

    @pytest.mark.asyncio
    async def test_raw_categories_populated(self) -> None:
        """Categories from API are set on raw_categories."""
        scraper = _make_scraper(RALEIGH_CONFIG, [SAMPLE_V1_RESPONSE])
        events = await scraper.scrape_all()
        assert "Family Fun" in events[0].raw_categories or "Festivals" in events[0].raw_categories

    @pytest.mark.asyncio
    async def test_excluded_categories_skips_matching_events(self) -> None:
        """Events whose categories intersect excluded_categories are dropped before yielding."""
        config = _make_config("v1", "visit-raleigh", "https://www.visitraleigh.com")
        config = config.model_copy(update={"selectors": {"rest_version": "v1", "excluded_categories": "nightlife,restaurants"}})
        response = {
            "docs": [
                {
                    "_id": "night1",
                    "title": "Bar Crawl Downtown",
                    "categories": [{"catName": "Nightlife"}],
                },
                {
                    "_id": "fam1",
                    "title": "Kids Science Fair",
                    "categories": [{"catName": "Family Fun"}],
                },
            ],
            "total": 2,
        }
        scraper = _make_scraper(config, [response])
        events = await scraper.scrape_all()
        assert len(events) == 1
        assert events[0].title == "Kids Science Fair"

    @pytest.mark.asyncio
    async def test_excluded_categories_case_insensitive(self) -> None:
        """Category matching is case-insensitive."""
        config = _make_config("v1", "visit-raleigh", "https://www.visitraleigh.com")
        config = config.model_copy(update={"selectors": {"rest_version": "v1", "excluded_categories": "Nightlife"}})
        response = {
            "docs": [{"_id": "n1", "title": "Late Night Bar", "categories": [{"catName": "nightlife"}]}],
            "total": 1,
        }
        scraper = _make_scraper(config, [response])
        events = await scraper.scrape_all()
        assert len(events) == 0

    @pytest.mark.asyncio
    async def test_no_excluded_categories_yields_all(self) -> None:
        """When excluded_categories is not set, no events are dropped."""
        scraper = _make_scraper(RALEIGH_CONFIG, [SAMPLE_V1_RESPONSE])
        events = await scraper.scrape_all()
        assert len(events) == 1
