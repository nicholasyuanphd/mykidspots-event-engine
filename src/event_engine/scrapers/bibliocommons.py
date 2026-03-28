"""BiblioCommonsScraper — adapter for BiblioCommons library RSS event feeds."""

import re
from collections.abc import AsyncIterator

from bs4 import BeautifulSoup

from event_engine.models import RawEvent
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register


@register
class BiblioCommonsScraper(BaseScraper):
    """Scrapes events from BiblioCommons RSS feeds.

    BiblioCommons is used by many US/Canadian library systems and provides
    RSS feeds at /{subdomain}.bibliocommons.com/events/rss/all.

    The RSS feed includes custom `bc:` namespace fields with structured data:
    - bc:start_date / bc:end_date — ISO 8601 UTC datetimes
    - bc:location — branch name, address, lat/lng
    - bc:is_cancelled — cancellation flag
    """

    platform = "bibliocommons"

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Fetch and parse the BiblioCommons RSS feed."""
        base = self.source.base_url.rstrip("/")
        url = f"{base}/events/rss/all"

        self.log.info("fetching_bibliocommons_rss", url=url)
        try:
            response = await self.fetch(url)
        except Exception as exc:
            self.log.warning("bibliocommons_fetch_failed", url=url, error=str(exc))
            return
        soup = BeautifulSoup(response.text, "lxml-xml")

        items = soup.find_all("item")
        self.log.info("rss_items_found", count=len(items))

        for item in items:
            event = self._parse_item(item)
            if event:
                yield event

    def _parse_item(self, item: object) -> RawEvent | None:
        """Parse a single RSS <item> into a RawEvent."""
        title_el = item.find("title")  # type: ignore[union-attr]
        if not title_el:
            return None
        title = title_el.get_text(strip=True)

        # Skip cancelled events
        cancelled_el = item.find("is_cancelled")  # type: ignore[union-attr]
        if cancelled_el and cancelled_el.get_text(strip=True).lower() == "true":
            return None

        # Link as external ID and source URL
        link_el = item.find("link")  # type: ignore[union-attr]
        link = link_el.get_text(strip=True) if link_el else ""

        # Extract event ID from URL or use link as fallback
        external_id = link.split("/")[-1] if link else title

        # Description (may contain HTML)
        desc_el = item.find("description")  # type: ignore[union-attr]
        description = ""
        if desc_el:
            # BiblioCommons wraps description in CDATA with HTML
            raw_desc = desc_el.get_text(strip=True)
            desc_soup = BeautifulSoup(raw_desc, "html.parser")
            description = desc_soup.get_text(separator="\n", strip=True)

        # --- Date/time: prefer bc:start_date (ISO 8601 UTC) ---
        raw_start = ""
        raw_end = ""

        bc_start = item.find("start_date")  # type: ignore[union-attr]
        if bc_start:
            raw_start = bc_start.get_text(strip=True)

        bc_end = item.find("end_date")  # type: ignore[union-attr]
        if bc_end:
            raw_end = bc_end.get_text(strip=True)

        # Fallback: pubDate (RFC 822) or description Date:/Time: patterns
        if not raw_start:
            pub_date_el = item.find("pubDate")  # type: ignore[union-attr]
            raw_start = pub_date_el.get_text(strip=True) if pub_date_el else ""

            date_match = re.search(r"Date:\s*(.+?)(?:\n|$)", description)
            time_match = re.search(r"Time:\s*(.+?)(?:\n|$)", description)
            if date_match:
                raw_start = date_match.group(1).strip()
                if time_match:
                    raw_start += " " + time_match.group(1).strip()

        # --- Location: extract from bc:location ---
        raw_location = ""
        raw_address = ""
        location_el = item.find("location")  # type: ignore[union-attr]
        if location_el:
            name_el = location_el.find("name")  # type: ignore[union-attr]
            if name_el:
                raw_location = name_el.get_text(strip=True)

            # Build address from bc:location fields
            number = location_el.find("number")  # type: ignore[union-attr]
            street = location_el.find("street")  # type: ignore[union-attr]
            city = location_el.find("city")  # type: ignore[union-attr]
            state = location_el.find("state")  # type: ignore[union-attr]
            zip_el = location_el.find("zip")  # type: ignore[union-attr]

            addr_parts = []
            if number:
                addr_parts.append(number.get_text(strip=True))
            if street:
                addr_parts.append(street.get_text(strip=True))
            street_str = " ".join(addr_parts)
            city_str = city.get_text(strip=True) if city else ""
            state_str = state.get_text(strip=True) if state else ""
            zip_str = zip_el.get_text(strip=True) if zip_el else ""

            if street_str:
                raw_address = f"{street_str}, {city_str}, {state_str} {zip_str}".strip()

        # Categories from RSS category tags
        raw_categories = []
        for cat in item.find_all("category"):  # type: ignore[union-attr]
            cat_text = cat.get_text(strip=True)
            if cat_text:
                raw_categories.append(cat_text)

        # Build raw_data with location coordinates for potential future use
        raw_data: dict = {"rss_title": title, "rss_link": link}
        if location_el:
            lat_el = location_el.find("latitude")  # type: ignore[union-attr]
            lng_el = location_el.find("longitude")  # type: ignore[union-attr]
            if lat_el:
                raw_data["latitude"] = lat_el.get_text(strip=True)
            if lng_el:
                raw_data["longitude"] = lng_el.get_text(strip=True)

        return RawEvent(
            source_id=self.source.id,
            external_id=external_id,
            title=title,
            description=description,
            raw_start=raw_start,
            raw_end=raw_end,
            raw_location=raw_location,
            raw_address=raw_address,
            raw_categories=raw_categories,
            source_url=link,
            raw_data=raw_data,
        )
