"""TheEventsCalendarScraper — adapter for WordPress The Events Calendar REST API.

The Events Calendar (TEC) is one of the most popular WordPress calendar plugins.
When its REST API is enabled, events are available at:
    {base_url}/wp-json/tribe/events/v1/events

The API returns paginated JSON with full event details including venue
geolocation, categories, and recurring event support.
"""

import re
from collections.abc import AsyncIterator
from datetime import date
from urllib.parse import urlencode

from event_engine.models import RawEvent
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register


@register
class TheEventsCalendarScraper(BaseScraper):
    """Scrapes events from WordPress The Events Calendar REST API.

    TEC exposes a well-structured JSON API at /wp-json/tribe/events/v1/events.
    Each event includes title, dates, venue with address and coordinates,
    categories, description, and image URL.

    Supports pagination via next_rest_url field in the response.
    """

    platform = "the_events_calendar"

    # Maximum pages to fetch to avoid infinite loops
    MAX_PAGES = 20
    # Events per page (TEC default max is 50)
    PER_PAGE = 50

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Fetch events from the TEC REST API with pagination."""
        base = self.source.base_url.rstrip("/")
        today = date.today().isoformat()

        params = {
            "per_page": str(self.PER_PAGE),
            "start_date": today,
            "status": "publish",
        }

        # If location_id is set, use it as category filter
        if self.source.location_id:
            params["categories"] = self.source.location_id

        url = f"{base}/wp-json/tribe/events/v1/events?{urlencode(params)}"

        event_count = 0
        page = 0
        seen_ids: set[str] = set()

        while url and page < self.MAX_PAGES:
            page += 1
            self.log.info("fetching_tec_page", url=url, page=page)

            try:
                data = await self.fetch_json(url)
            except Exception:
                self.log.warning("tec_fetch_failed", url=url, page=page)
                break

            if not isinstance(data, dict):
                self.log.warning("unexpected_response_format", type=type(data).__name__)
                break

            events = data.get("events", [])
            if not events:
                break

            for item in events:
                event_id = str(item.get("id", ""))
                if not event_id or event_id in seen_ids:
                    continue
                seen_ids.add(event_id)

                event = self._parse_event(item)
                if event:
                    event_count += 1
                    yield event

            # Follow pagination
            next_url = data.get("next_rest_url", "")
            if next_url and next_url != url:
                url = next_url
            else:
                break

        self.log.info("tec_scrape_complete", total_events=event_count, pages=page)

    def _parse_event(self, item: dict) -> RawEvent | None:
        """Parse a single TEC event from the API response."""
        event_id = str(item.get("id", ""))
        title = item.get("title", "").strip()

        if not event_id or not title:
            return None

        # Strip HTML tags from title (TEC sometimes includes them)
        title = re.sub(r"<[^>]+>", "", title).strip()

        # Dates
        raw_start = item.get("start_date", "") or ""
        raw_end = item.get("end_date", "") or ""

        # Venue
        venue = item.get("venue", {}) or {}
        raw_location = venue.get("venue", "") or ""
        raw_address = ""
        if venue:
            parts = [
                venue.get("address", ""),
                venue.get("city", ""),
                venue.get("state", ""),
                venue.get("zip", ""),
            ]
            raw_address = ", ".join(p for p in parts if p)

        # Description — strip HTML tags for raw text
        description = item.get("description", "") or ""
        description = re.sub(r"<[^>]+>", " ", description).strip()
        # Collapse multiple whitespace
        description = re.sub(r"\s+", " ", description)

        # Categories
        raw_categories = []
        categories = item.get("categories", [])
        if isinstance(categories, list):
            for cat in categories:
                if isinstance(cat, dict):
                    name = cat.get("name", "")
                    if name:
                        raw_categories.append(name)
                elif isinstance(cat, str):
                    raw_categories.append(cat)

        # URL
        source_url = item.get("url", "") or ""

        # Image
        image_url = ""
        image = item.get("image", {}) or {}
        if isinstance(image, dict):
            image_url = image.get("url", "") or ""

        # Cost
        raw_cost_text = ""
        cost = item.get("cost", "")
        if cost:
            raw_cost_text = str(cost)
        cost_details = item.get("cost_details", {}) or {}
        if isinstance(cost_details, dict):
            values = cost_details.get("values", [])
            if values:
                raw_cost_text = ", ".join(str(v) for v in values)

        # Recurring
        is_recurring = bool(item.get("recurring", False))

        return RawEvent(
            source_id=self.source.id,
            external_id=event_id,
            title=title,
            description=description,
            raw_start=raw_start,
            raw_end=raw_end,
            raw_location=raw_location,
            raw_address=raw_address,
            raw_categories=raw_categories,
            raw_cost_text=raw_cost_text,
            source_url=source_url,
            image_url=image_url,
            is_recurring=is_recurring,
            raw_data=item,
        )
