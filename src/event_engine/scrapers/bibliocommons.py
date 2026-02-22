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
    """

    platform = "bibliocommons"

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Fetch and parse the BiblioCommons RSS feed."""
        base = self.source.base_url.rstrip("/")
        url = f"{base}/events/rss/all"

        self.log.info("fetching_bibliocommons_rss", url=url)
        response = await self.fetch(url)
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

        # Publication date (RFC 822 format)
        pub_date_el = item.find("pubDate")  # type: ignore[union-attr]
        raw_start = pub_date_el.get_text(strip=True) if pub_date_el else ""

        # Try to extract date/time from description
        # BiblioCommons often includes "Date: ...", "Time: ..." in description
        date_match = re.search(r"Date:\s*(.+?)(?:\n|$)", description)
        time_match = re.search(r"Time:\s*(.+?)(?:\n|$)", description)
        if date_match:
            raw_start = date_match.group(1).strip()
            if time_match:
                raw_start += " " + time_match.group(1).strip()

        # Categories from RSS category tags
        raw_categories = []
        for cat in item.find_all("category"):  # type: ignore[union-attr]
            cat_text = cat.get_text(strip=True)
            if cat_text:
                raw_categories.append(cat_text)

        return RawEvent(
            source_id=self.source.id,
            external_id=external_id,
            title=title,
            description=description,
            raw_start=raw_start,
            raw_categories=raw_categories,
            source_url=link,
            raw_data={"rss_title": title, "rss_link": link},
        )
