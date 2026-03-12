"""WakeGovScraper — adapter for Wake County Government events (wake.gov)."""

import math
import re
from collections.abc import AsyncIterator
from urllib.parse import urlencode

from bs4 import BeautifulSoup, Tag

from event_engine.models import RawEvent
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register

EVENTS_PER_PAGE = 18


@register
class WakeGovScraper(BaseScraper):
    """Scrapes events from wake.gov (Drupal-based event listings).

    Uses list page scraping only — avoids fetching individual detail pages.
    Location data comes from the SourceConfig YAML (known library addresses).
    """

    platform = "wakegov"

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Scrape all events from a Wake County library location."""
        page = 0
        total_pages = 1  # Will be updated after first fetch

        while page < total_pages:
            params: dict[str, str | int] = {
                "field_department_target_id": self.source.department_id,
                "location": self.source.location_id,
                "page": page,
            }
            url = f"{self.source.base_url}?{urlencode(params)}"

            self.log.info("fetching_page", url=url, page=page, total_pages=total_pages)
            response = await self.fetch(url)
            soup = BeautifulSoup(response.text, "lxml")

            # Update total pages from results header on first page
            if page == 0:
                total_pages = self._parse_total_pages(soup)
                self.log.info("total_pages_detected", total_pages=total_pages)

            # Parse event cards
            articles = soup.select('article[about^="/events/"]')
            if not articles:
                self.log.warning("no_events_on_page", page=page)
                break

            for article in articles:
                event = self._parse_card(article)
                if event:
                    yield event

            page += 1

    def _parse_total_pages(self, soup: BeautifulSoup) -> int:
        """Extract total page count from the results header."""
        results_header = soup.select_one("header.events--results")
        if not results_header:
            return 1

        text = results_header.get_text(strip=True)
        # "Displaying 1 - 18 of 1639"
        match = re.search(r"of\s+([\d,]+)", text)
        if match:
            total = int(match.group(1).replace(",", ""))
            return max(1, math.ceil(total / EVENTS_PER_PAGE))
        return 1

    def _parse_card(self, article: Tag) -> RawEvent | None:
        """Parse a single event card from the listing page."""
        # Extract slug from article[about] attribute
        about = article.get("about", "")
        if not about or not isinstance(about, str):
            return None
        slug = about.strip("/").split("/")[-1]

        # Title
        title_el = article.select_one(".eventbrite-card-body h2 a span")
        if not title_el:
            return None
        title = title_el.get_text(strip=True)

        # Date and time from .date-time
        date_time_el = article.select_one(".date-time p")
        raw_start = ""
        raw_end = ""
        if date_time_el:
            raw_start, raw_end = self._parse_date_time(date_time_el)

        # Location name
        location_el = article.select_one(".eventbrite-card-location a")
        raw_location = location_el.get_text(strip=True) if location_el else ""

        # Category
        category_el = article.select_one(".eventbrite-department .department-name")
        raw_categories = []
        if category_el:
            cat_text = category_el.get_text(strip=True)
            if cat_text:
                raw_categories = [cat_text]

        # Image URL — extracted for completeness but discarded by the normalizer
        # (pipeline.py always sets image_urls=[] to avoid copyright risk).
        # Kept here so RawEvent is fully populated if policy changes in the future.
        img_el = article.select_one(".eventbrite-card-image img")
        image_url = ""
        if img_el:
            image_url = img_el.get("src", "")
            if isinstance(image_url, list):
                image_url = image_url[0] if image_url else ""

        # Source URL
        source_url = f"https://www.wake.gov{about}"

        # Detect recurring events: date ranges like "Jan 6, 2026 - Dec 1, 2026"
        is_recurring = False
        if raw_start and " - " in self._get_date_strong_text(date_time_el):
            is_recurring = True

        return RawEvent(
            source_id=self.source.id,
            external_id=slug,
            title=title,
            raw_start=raw_start,
            raw_end=raw_end,
            raw_location=raw_location,
            raw_categories=raw_categories,
            source_url=source_url,
            image_url=image_url,
            is_recurring=is_recurring,
            raw_data={"slug": slug, "about": about},
        )

    def _parse_date_time(self, p_tag: Tag) -> tuple[str, str]:
        """Parse date and time from the .date-time <p> element.

        The <p> contains:
          <strong>February 20, 2026</strong>  (or date range with " - ")
          <br>
          11:00 am - 12:00 pm

        Returns (raw_start, raw_end) as combined date+time strings.
        """
        # Get the date from <strong>
        strong = p_tag.find("strong")
        date_text = strong.get_text(strip=True) if strong else ""

        # Get the time from text after <br>
        # Extract all text, remove the date part
        full_text = p_tag.get_text(separator="\n", strip=True)
        lines = [line.strip() for line in full_text.split("\n") if line.strip()]

        time_text = ""
        for line in lines:
            # Time lines contain "am" or "pm"
            if re.search(r"\d+:\d+\s*(am|pm)", line, re.IGNORECASE):
                time_text = line
                break

        # For date ranges ("Jan 6, 2026 - Dec 1, 2026"), use first date
        start_date = date_text
        if " - " in date_text:
            parts = date_text.split(" - ")
            start_date = parts[0].strip()

        # Parse time range
        start_time = ""
        end_time = ""
        if time_text:
            time_match = re.match(
                r"(\d+:\d+\s*(?:am|pm))\s*-\s*(\d+:\d+\s*(?:am|pm))",
                time_text,
                re.IGNORECASE,
            )
            if time_match:
                start_time = time_match.group(1).strip()
                end_time = time_match.group(2).strip()
            else:
                start_time = time_text.strip()

        raw_start = f"{start_date} {start_time}".strip()
        raw_end = f"{start_date} {end_time}".strip() if end_time else ""

        return raw_start, raw_end

    def _get_date_strong_text(self, p_tag: Tag | None) -> str:
        """Get the raw text from the <strong> date element."""
        if not p_tag:
            return ""
        strong = p_tag.find("strong")
        return strong.get_text(strip=True) if strong else ""
