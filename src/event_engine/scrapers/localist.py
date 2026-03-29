"""LocalistScraper — adapter for Localist event platform JSON API.

Localist is used by universities, government agencies, and organizations.
The NC DNCR calendar (events.dncr.nc.gov) uses this platform for
state parks, museums, aquariums, NC Zoo, and historic sites.

API docs: https://developer.localist.com/doc/api
Endpoint: {base_url}/api/2/events?days=90&pp=100&page=N
"""

import re
from collections.abc import AsyncIterator
from urllib.parse import urlencode

from event_engine.models import RawEvent
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register


@register
class LocalistScraper(BaseScraper):
    """Scrapes events from Localist-powered event calendars via JSON API.

    Localist is used by universities, government agencies, and organizations.
    The NC DNCR calendar (events.dncr.nc.gov) uses this platform for
    state parks, museums, aquariums, NC Zoo, and historic sites.
    """

    platform = "localist"

    # Maximum pages to fetch to avoid infinite loops
    MAX_PAGES = 50
    # Events per page (Localist max is 100)
    PER_PAGE = 100
    # How many days ahead to look
    DAYS_AHEAD = 90

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Fetch events from the Localist JSON API with pagination."""
        base = self.source.base_url.rstrip("/")

        params = {
            "pp": str(self.PER_PAGE),
            "days": str(self.DAYS_AHEAD),
            "page": "1",
        }

        event_count = 0
        page = 0
        seen_ids: set[str] = set()

        while page < self.MAX_PAGES:
            page += 1
            params["page"] = str(page)
            url = f"{base}/api/2/events?{urlencode(params)}"

            self.log.info("fetching_localist_page", url=url, page=page)

            try:
                data = await self.fetch_json(url)
            except Exception:
                self.log.warning("localist_fetch_failed", url=url, page=page)
                break

            if not isinstance(data, dict):
                self.log.warning("unexpected_response_format", type=type(data).__name__)
                break

            # Events are wrapped: [{"event": {...}}, ...]
            events = data.get("events", [])
            if not events:
                break

            for wrapper in events:
                item = wrapper.get("event", {}) if isinstance(wrapper, dict) else {}
                if not item:
                    continue

                event_id = str(item.get("id", ""))
                if not event_id or event_id in seen_ids:
                    continue
                seen_ids.add(event_id)

                event = self._parse_event(item)
                if event:
                    event_count += 1
                    yield event

            # Check pagination: page.total = total number of pages
            page_info = data.get("page", {})
            total_pages = page_info.get("total", 1)
            if page >= total_pages:
                break

        self.log.info("localist_scrape_complete", total_events=event_count, pages=page)

    def _parse_event(self, item: dict) -> RawEvent | None:
        """Parse a single Localist event from the API response."""
        event_id = str(item.get("id", ""))
        title = (item.get("title") or "").strip()

        if not event_id or not title:
            return None

        # Strip HTML tags from title if any
        title = re.sub(r"<[^>]+>", "", title).strip()

        # --- Dates ---
        # Use the first event_instance for precise start/end times
        instances = item.get("event_instances", [])
        raw_start = ""
        raw_end = ""
        if instances:
            first_inst = instances[0].get("event_instance", {})
            raw_start = first_inst.get("start") or ""
            raw_end = first_inst.get("end") or ""
        # Fallback to first_date if no instances
        if not raw_start:
            raw_start = item.get("first_date") or ""

        if not raw_start:
            return None

        # --- Location ---
        location_name = (item.get("location_name") or "").strip()
        raw_address = (item.get("address") or "").strip()

        # --- Geo coordinates ---
        geo = item.get("geo") or {}
        geo_lat = geo.get("latitude")
        geo_lng = geo.get("longitude")
        geo_city = geo.get("city") or ""

        # Build address from geo if event address is missing
        if not raw_address and geo:
            parts = [
                geo.get("street") or "",
                geo.get("city") or "",
                geo.get("state") or "",
                geo.get("zip") or "",
            ]
            raw_address = ", ".join(p for p in parts if p)

        # --- Description ---
        description = item.get("description_text") or ""
        # Clean up whitespace
        description = re.sub(r"\s+", " ", description).strip()

        # --- Categories from filters ---
        raw_categories: list[str] = []
        filters = item.get("filters") or {}

        for type_entry in filters.get("event_types", []):
            name = type_entry.get("name", "")
            if name:
                raw_categories.append(name)

        # --- Age/audience from filters ---
        raw_age_text = ""
        audiences = filters.get("event_audiences", [])
        audience_names = [a.get("name", "") for a in audiences if a.get("name")]
        if audience_names:
            raw_age_text = ", ".join(audience_names)

        # --- Cost ---
        is_free = item.get("free", False)
        ticket_cost = (item.get("ticket_cost") or "").strip()

        if is_free:
            raw_cost_text = "Free"
        elif ticket_cost:
            raw_cost_text = ticket_cost
        else:
            # Many DNCR events have free=False but no ticket_cost — treat as free
            raw_cost_text = "Free"

        # --- URLs ---
        source_url = item.get("localist_url") or item.get("url") or ""
        image_url = item.get("photo_url") or ""

        # --- Registration ---
        registration_url = ""
        ticket_url = (item.get("ticket_url") or "").strip()
        if ticket_url:
            registration_url = ticket_url

        # --- Recurring ---
        is_recurring = bool(item.get("recurring", False))
        recurrence_text = ""
        if is_recurring:
            first_date = item.get("first_date") or ""
            last_date = item.get("last_date") or ""
            if first_date and last_date and first_date != last_date:
                recurrence_text = f"{first_date} to {last_date}"

        # --- Raw data: store geo for per-event coordinates ---
        raw_data: dict = {}
        if geo_lat:
            raw_data["latitude"] = geo_lat
        if geo_lng:
            raw_data["longitude"] = geo_lng
        if geo_city:
            raw_data["geo_city"] = geo_city

        return RawEvent(
            source_id=self.source.id,
            external_id=event_id,
            title=title,
            description=description,
            raw_start=raw_start,
            raw_end=raw_end,
            raw_location=location_name,
            raw_address=raw_address,
            raw_categories=raw_categories,
            raw_age_text=raw_age_text,
            raw_cost_text=raw_cost_text,
            source_url=source_url,
            image_url=image_url,
            registration_url=registration_url,
            is_recurring=is_recurring,
            recurrence_text=recurrence_text,
            raw_data=raw_data,
        )
