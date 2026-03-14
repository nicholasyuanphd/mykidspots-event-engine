"""RevizeScraper — adapter for Revize CMS calendar endpoints.

Revize is a government website CMS that exposes calendar events via a
FullCalendar-compatible JSON feed at:
    /revize/plugins/revize_calendar/revize_calendar_ajax.jsp?cid=<ids>&start=<date>&end=<date>

NOTE: Many Revize sites are behind Cloudflare WAF which blocks automated
requests. Sources that cannot be reached should have enabled=false in YAML.
"""

from collections.abc import AsyncIterator
from datetime import date, timedelta

from event_engine.models import RawEvent
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register


@register
class RevizeScraper(BaseScraper):
    """Scrapes events from Revize CMS calendar AJAX endpoints.

    Configuration via SourceConfig:
    - base_url: The site origin (e.g., "https://townofwendellnc.gov")
    - location_id: Comma-separated calendar IDs (e.g., "1,2,3,6")
    - selectors["ajax_path"]: Override AJAX path (default: standard Revize path)
    """

    platform = "revize"

    AJAX_PATH = "/revize/plugins/revize_calendar/revize_calendar_ajax.jsp"

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Fetch events from the Revize calendar AJAX endpoint."""
        today = date.today()
        end = today + timedelta(days=90)

        ajax_path = self.source.selectors.get("ajax_path", self.AJAX_PATH)
        cid = self.source.location_id or "1"

        url = (
            f"{self.source.base_url.rstrip('/')}{ajax_path}"
            f"?cid={cid}&start={today.isoformat()}&end={end.isoformat()}"
        )

        self.log.info("fetching_revize_calendar", url=url)

        try:
            data = await self.fetch_json(url)
        except Exception as exc:
            self.log.error("revize_fetch_failed", url=url, error=str(exc))
            return

        if not isinstance(data, list):
            self.log.warning("revize_response_not_list", type=type(data).__name__)
            return

        self.log.info("revize_events_found", count=len(data))

        seen_ids: set[str] = set()
        for event_data in data:
            raw_event = self._parse_event(event_data)
            if raw_event is None:
                continue
            if raw_event.external_id in seen_ids:
                continue
            seen_ids.add(raw_event.external_id)
            yield raw_event

    def _parse_event(self, data: dict) -> RawEvent | None:
        """Parse a Revize FullCalendar JSON event into a RawEvent.

        Revize event objects typically have:
        - title: Event name
        - id: Numeric event ID
        - start: ISO 8601 datetime
        - end: ISO 8601 datetime (optional)
        - url: Relative event detail URL
        - allDay: Boolean
        - description: Event description (sometimes present)
        - className: CSS class for calendar category color
        """
        title = (data.get("title") or "").strip()
        if not title:
            return None

        event_id = str(data.get("id", "")).strip()
        if not event_id:
            return None

        raw_start = data.get("start", "")
        raw_end = data.get("end", "")

        # Build source URL
        url_path = data.get("url", "")
        source_url = ""
        if url_path:
            if url_path.startswith("http"):
                source_url = url_path
            else:
                source_url = f"{self.source.base_url.rstrip('/')}{url_path}"

        description = data.get("description", "")

        return RawEvent(
            source_id=self.source.id,
            external_id=event_id,
            title=title,
            description=description,
            raw_start=raw_start,
            raw_end=raw_end if raw_end else "",
            raw_location=self.source.location.name,
            raw_address=self.source.location.address or "",
            source_url=source_url,
            is_recurring=False,
            raw_data=data,
        )
