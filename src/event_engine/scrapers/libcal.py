"""LibCalScraper — adapter for Springshare LibCal library event calendars.

LibCal powers ~60% of US public library event calendars. Events are available
via a JSON API at /{subdomain}.libcal.com/ajax/calendar/{calendar_id}.
"""

from collections.abc import AsyncIterator
from urllib.parse import urlencode

from event_engine.models import RawEvent
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register


@register
class LibCalScraper(BaseScraper):
    """Scrapes events from LibCal JSON API.

    LibCal returns events as JSON from its AJAX endpoint. Each event has
    a well-structured payload with title, dates, location, category, and
    a unique event ID — making it the cleanest adapter.
    """

    platform = "libcal"

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Fetch events from the LibCal JSON API."""
        # LibCal AJAX endpoint: /ajax/calendar/{calendar_id}
        # Returns JSON array of event objects
        base = self.source.base_url.rstrip("/")
        cal_id = self.source.location_id

        # Fetch current and next month to ensure we capture upcoming events
        url = f"{base}/ajax/calendar/{cal_id}"
        params: dict[str, str] = {}

        self.log.info("fetching_libcal", url=url, calendar_id=cal_id)
        data = await self.fetch_json(f"{url}?{urlencode(params)}")

        if not isinstance(data, list):
            self.log.warning("unexpected_response_format", type=type(data).__name__)
            return

        for item in data:
            event = self._parse_event(item)
            if event:
                yield event

    def _parse_event(self, item: dict) -> RawEvent | None:
        """Parse a single LibCal event from the JSON response."""
        # LibCal event IDs are numeric
        event_id = str(item.get("id", ""))
        title = item.get("title", "").strip()

        if not event_id or not title:
            return None

        # Dates: LibCal provides ISO 8601 strings
        raw_start = item.get("start", "")
        raw_end = item.get("end", "")

        # Location
        location = item.get("location", {})
        raw_location = ""
        if isinstance(location, dict):
            raw_location = location.get("name", "")
        elif isinstance(location, str):
            raw_location = location

        # Categories
        raw_categories = []
        categories = item.get("category", [])
        if isinstance(categories, list):
            for cat in categories:
                if isinstance(cat, dict):
                    raw_categories.append(cat.get("name", ""))
                elif isinstance(cat, str):
                    raw_categories.append(cat)
        elif isinstance(categories, str):
            raw_categories = [categories]

        # Description
        description = item.get("description", "") or ""

        # URL
        source_url = item.get("url", {})
        if isinstance(source_url, dict):
            source_url = source_url.get("public", "")
        elif not isinstance(source_url, str):
            source_url = ""

        # Image
        image_url = item.get("featured_image", "") or ""

        # Registration URL
        registration_url = item.get("registration", "") or ""

        # Age text: check for audience or age fields
        raw_age_text = ""
        audience = item.get("audience", [])
        if isinstance(audience, list):
            raw_age_text = ", ".join(
                a.get("name", "") if isinstance(a, dict) else str(a) for a in audience
            )
        elif isinstance(audience, str):
            raw_age_text = audience

        return RawEvent(
            source_id=self.source.id,
            external_id=event_id,
            title=title,
            description=description,
            raw_start=raw_start,
            raw_end=raw_end,
            raw_location=raw_location,
            raw_categories=raw_categories,
            raw_age_text=raw_age_text,
            raw_cost_text=item.get("cost", "Free"),
            source_url=source_url,
            image_url=image_url,
            registration_url=registration_url,
            is_recurring=bool(item.get("recurring")),
            raw_data=item,
        )
