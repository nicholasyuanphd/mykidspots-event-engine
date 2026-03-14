"""DrupalFullCalendarScraper — adapter for Drupal sites using FullCalendar views.

Supports two modes:
1. Inline events: Events embedded in drupalSettings.fullCalendarView[0].calendar_options.events
   (e.g., Knightdale — older fullcalendar_view Drupal module)
2. JSON feed: Events fetched from a vc3-fullcalendar-events-feed endpoint
   (e.g., Wake Forest — vc3_fullcalendar Drupal module)

Both modes yield FullCalendar-compatible JSON event objects with ISO 8601 datetimes.
"""

import json
import re
from collections.abc import AsyncIterator
from datetime import date, timedelta

from event_engine.models import RawEvent
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register


@register
class DrupalFullCalendarScraper(BaseScraper):
    """Scrapes events from Drupal sites that use FullCalendar views.

    Configuration via SourceConfig:
    - base_url: The calendar page URL (inline mode) or the JSON feed base URL (feed mode)
    - selectors["mode"]: "inline" (default) or "feed"
    - selectors["feed_url"]: Feed endpoint path (feed mode, e.g. "/vc3-fullcalendar-events-feed")
    - selectors["feed_params"]: Extra query params as "key=val&key2=val2" (optional)
    """

    platform = "drupal_fullcalendar"

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Scrape events from a Drupal FullCalendar source."""
        mode = self.source.selectors.get("mode", "inline")

        if mode == "feed":
            events_json = await self._fetch_feed_events()
        else:
            events_json = await self._fetch_inline_events()

        seen_ids: set[str] = set()
        for event_data in events_json:
            raw_event = self._parse_event(event_data)
            if raw_event is None:
                continue
            if raw_event.external_id in seen_ids:
                continue
            seen_ids.add(raw_event.external_id)
            yield raw_event

    async def _fetch_inline_events(self) -> list[dict]:
        """Extract events from drupalSettings JSON embedded in the calendar page HTML."""
        self.log.info("fetching_inline_calendar", url=self.source.base_url)
        response = await self.fetch(self.source.base_url)
        html = response.text

        # Extract drupalSettings JSON from <script data-drupal-selector="drupal-settings-json">
        match = re.search(
            r'data-drupal-selector="drupal-settings-json">\s*({.*?})\s*</script>',
            html,
            re.DOTALL,
        )
        if not match:
            self.log.warning("no_drupal_settings_found")
            return []

        try:
            settings = json.loads(match.group(1))
        except json.JSONDecodeError:
            self.log.warning("invalid_drupal_settings_json")
            return []

        # Navigate to fullCalendarView[0].calendar_options.events
        fc_views = settings.get("fullCalendarView", [])
        if not fc_views:
            self.log.warning("no_fullcalendar_view")
            return []

        calendar_options_raw = fc_views[0].get("calendar_options", "")
        if not calendar_options_raw:
            self.log.warning("no_calendar_options")
            return []

        try:
            calendar_options = json.loads(calendar_options_raw)
        except json.JSONDecodeError:
            self.log.warning("invalid_calendar_options_json")
            return []

        events = calendar_options.get("events", [])
        self.log.info("inline_events_found", count=len(events))
        return events  # type: ignore[no-any-return]

    async def _fetch_feed_events(self) -> list[dict]:
        """Fetch events from a vc3-fullcalendar JSON feed endpoint."""
        feed_path = self.source.selectors.get("feed_url", "/vc3-fullcalendar-events-feed")
        extra_params = self.source.selectors.get("feed_params", "")

        # Fetch 90 days of events in a single request
        today = date.today()
        end = today + timedelta(days=90)

        # Build URL
        base = self.source.base_url.rstrip("/")
        url = f"{base}{feed_path}?start={today.isoformat()}&end={end.isoformat()}"
        if extra_params:
            url += f"&{extra_params}"

        self.log.info("fetching_feed", url=url)
        data = await self.fetch_json(url)

        if not isinstance(data, list):
            self.log.warning("feed_response_not_list", type=type(data).__name__)
            return []

        self.log.info("feed_events_found", count=len(data))
        return data  # type: ignore[return-value]

    def _parse_event(self, data: dict) -> RawEvent | None:
        """Parse a FullCalendar JSON event object into a RawEvent."""
        title = (data.get("title") or "").strip()
        if not title:
            return None

        # Build external_id from eid (inline) or id (feed)
        eid = data.get("eid", "")
        event_id = data.get("id", "")
        external_id = str(eid or event_id).strip()
        if not external_id:
            return None

        # Start/end times — ISO 8601 format
        raw_start = data.get("start", "")
        raw_end = data.get("end", "")

        # Event URL
        url_path = data.get("url", "")
        source_url = ""
        if url_path:
            if url_path.startswith("http"):
                source_url = url_path
            else:
                # Build absolute URL from base
                base = self.source.base_url.rstrip("/")
                # Strip path from base_url to get origin
                origin = re.match(r"https?://[^/]+", base)
                if origin:
                    source_url = f"{origin.group(0)}{url_path}"

        return RawEvent(
            source_id=self.source.id,
            external_id=external_id,
            title=title,
            raw_start=raw_start,
            raw_end=raw_end if raw_end else "",
            raw_location=self.source.location.name,
            raw_address=self.source.location.address or "",
            source_url=source_url,
            is_recurring=False,
            raw_data=data,
        )
