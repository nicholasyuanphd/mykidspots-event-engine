"""ICalScraper — adapter for generic iCal (.ics) event feeds."""

from collections.abc import AsyncIterator
from datetime import datetime

from icalendar import Calendar

from event_engine.models import RawEvent
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register


@register
class ICalScraper(BaseScraper):
    """Scrapes events from iCal (.ics) feeds.

    Many venues and organizations publish event calendars as .ics feeds.
    This adapter parses VEVENT components into RawEvent objects.
    """

    platform = "ical"

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Fetch and parse an iCal feed."""
        url = self.source.base_url
        self.log.info("fetching_ical", url=url)

        response = await self.fetch(url)
        cal = Calendar.from_ical(response.text)

        event_count = 0
        for component in cal.walk():
            if component.name != "VEVENT":
                continue

            event = self._parse_vevent(component)
            if event:
                event_count += 1
                yield event

        self.log.info("ical_events_parsed", count=event_count)

    def _parse_vevent(self, vevent: object) -> RawEvent | None:
        """Parse a VEVENT component into a RawEvent."""
        # UID is the standard iCal unique identifier
        uid = str(vevent.get("UID", ""))  # type: ignore[union-attr]
        summary = str(vevent.get("SUMMARY", ""))  # type: ignore[union-attr]

        if not uid or not summary:
            return None

        # Start/end times
        dtstart = vevent.get("DTSTART")  # type: ignore[union-attr]
        dtend = vevent.get("DTEND")  # type: ignore[union-attr]

        raw_start = ""
        if dtstart:
            dt = dtstart.dt
            raw_start = dt.isoformat() if isinstance(dt, datetime) else str(dt)

        raw_end = ""
        if dtend:
            dt = dtend.dt
            raw_end = dt.isoformat() if isinstance(dt, datetime) else str(dt)

        # Description
        description = str(vevent.get("DESCRIPTION", "") or "")  # type: ignore[union-attr]

        # Location
        raw_location = str(vevent.get("LOCATION", "") or "")  # type: ignore[union-attr]

        # URL
        source_url = str(vevent.get("URL", "") or "")  # type: ignore[union-attr]

        # Categories
        raw_categories = []
        categories = vevent.get("CATEGORIES")  # type: ignore[union-attr]
        if categories:
            cats = categories.to_ical().decode("utf-8", errors="replace")
            raw_categories = [c.strip() for c in cats.split(",") if c.strip()]

        # Recurrence
        is_recurring = bool(vevent.get("RRULE"))  # type: ignore[union-attr]
        recurrence_text = ""
        rrule = vevent.get("RRULE")  # type: ignore[union-attr]
        if rrule:
            recurrence_text = rrule.to_ical().decode("utf-8", errors="replace")

        return RawEvent(
            source_id=self.source.id,
            external_id=uid,
            title=summary.strip(),
            description=description.strip(),
            raw_start=raw_start,
            raw_end=raw_end,
            raw_location=raw_location,
            raw_categories=raw_categories,
            source_url=source_url,
            is_recurring=is_recurring,
            recurrence_text=recurrence_text,
            raw_data={"uid": uid},
        )
