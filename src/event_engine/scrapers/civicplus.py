"""CivicPlusScraper — adapter for CivicPlus HTML list-view event calendars."""

import re
from collections.abc import AsyncIterator
from datetime import date, timedelta
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

from event_engine.models import RawEvent
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register


@register
class CivicPlusScraper(BaseScraper):
    """Scrapes events from CivicPlus HTML list-view calendars.

    Many US municipal websites (parks depts, town halls) run CivicPlus CMS,
    which exposes a list-view calendar at:
        {base_url}?keyword=&CID={location_id}&startdate=MM%2FDD%2FYYYY&enddate=MM%2FDD%2FYYYY&view=list

    Scrapes 3 rolling 30-day windows (days 0-30, 30-60, 60-90) and deduplicates
    events by EID across windows.
    """

    platform = "civicplus"

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Scrape all events across 3 rolling 30-day windows."""
        seen_eids: set[str] = set()
        today = date.today()

        for window in range(3):
            start = today + timedelta(days=window * 30)
            end = today + timedelta(days=(window + 1) * 30)

            url = self._build_url(start, end)
            self.log.info("fetching_window", url=url, window=window)

            try:
                response = await self.fetch(url)
                soup = BeautifulSoup(response.text, "lxml")

                for item in soup.select("ul.list-group li.list-group-item"):
                    event = self._parse_item(item)
                    if event is None:
                        continue
                    if event.external_id in seen_eids:
                        continue
                    seen_eids.add(event.external_id)
                    yield event
            except Exception as exc:
                self.log.warning("window_fetch_failed", window=window, url=url, error=str(exc))
                continue

    def _build_url(self, start: date, end: date) -> str:
        """Build a CivicPlus list-view URL for the given date range."""
        location_id = self.source.location_id or "0"
        start_str = start.strftime("%m%%2F%d%%2F%Y")
        end_str = end.strftime("%m%%2F%d%%2F%Y")
        return (
            f"{self.source.base_url}"
            f"?keyword=&CID={location_id}"
            f"&startdate={start_str}&enddate={end_str}&view=list"
        )

    def _parse_item(self, item: Tag) -> RawEvent | None:
        """Parse a single <li class="list-group-item"> into a RawEvent."""
        # Title and href
        link = item.select_one("h3.list-group-item-heading a")
        if not link:
            return None

        title = link.get_text(strip=True)
        if not title:
            return None

        raw_href = link.get("href", "") or ""
        href: str = raw_href[0] if isinstance(raw_href, list) else str(raw_href)

        # Extract EID from href: /Calendar.aspx?EID=13717&...
        external_id = self._extract_eid(str(href))
        if not external_id:
            return None

        # Build absolute source URL from origin of base_url
        origin = self._get_origin()
        source_url = f"{origin}{href}" if href.startswith("/") else str(href)

        # All <p class="list-group-item-text"> paragraphs
        paragraphs = item.select("p.list-group-item-text")

        raw_start = ""
        raw_end = ""
        location_name = self.source.location.name

        for p in paragraphs:
            text = p.get_text(strip=True)
            if text.startswith("@"):
                # Location paragraph
                location_name = text[1:].strip()
            elif raw_start == "":
                # First non-location paragraph is the date/time
                raw_start, raw_end = self._split_datetime(text)

        return RawEvent(
            source_id=self.source.id,
            external_id=external_id,
            title=title,
            raw_start=raw_start,
            raw_end=raw_end if raw_end else "",
            raw_location=location_name,
            raw_address=self.source.location.address or "",
            is_recurring=False,
            description="",
            source_url=source_url,
        )

    def _extract_eid(self, href: str) -> str:
        """Extract the EID query parameter from a CivicPlus calendar href."""
        try:
            parsed = urlparse(href)
            params = parse_qs(parsed.query)
            eid_list = params.get("EID", [])
            return eid_list[0] if eid_list else ""
        except Exception:
            return ""

    def _get_origin(self) -> str:
        """Return the scheme+host of base_url (e.g., 'https://www.hollyspringsnc.gov')."""
        parsed = urlparse(self.source.base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _split_datetime(self, text: str) -> tuple[str, str]:
        """Split a CivicPlus datetime string into (raw_start, raw_end).

        Handles three formats:
          1. Timed range:  "March 14, 2026, 9:00 AM - 12:00 PM"
             → ("March 14, 2026 9:00 AM", "March 14, 2026 12:00 PM")
          2. Timed no-end: "March 14, 2026, 6:00 PM"
             → ("March 14, 2026 6:00 PM", "")
          3. All-day:      "March 16, 2026"
             → ("March 16, 2026", "")
        """
        text = text.strip()

        # Guard: if no time component present, return as-is
        if not re.search(r"\d{1,2}:\d{2}", text):
            return (text, "")

        # Split on the comma that separates the date from the time portion.
        # CivicPlus format: "Month DD, YYYY, HH:MM AM/PM[ - HH:MM AM/PM]"
        # The date ends at the second comma (after the year).
        parts = text.split(",", 2)
        if len(parts) < 3:
            # Unexpected format — return whole text as raw_start
            return (text, "")

        date_part = f"{parts[0].strip()}, {parts[1].strip()}"  # "March 14, 2026"
        time_part = parts[2].strip()  # "9:00 AM - 12:00 PM" or "6:00 PM"

        # Check for a time range: "9:00 AM - 12:00 PM"
        range_match = re.match(
            r"(\d{1,2}:\d{2}\s*[APap][Mm])\s*-\s*(\d{1,2}:\d{2}\s*[APap][Mm])",
            time_part,
        )
        if range_match:
            start_time = range_match.group(1).strip()
            end_time = range_match.group(2).strip()
            return (f"{date_part} {start_time}", f"{date_part} {end_time}")

        # Single time (no range)
        return (f"{date_part} {time_part}", "")
