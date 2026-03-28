"""LibraryMarketScraper — adapter for LibraryMarket (librarycalendar.com) event calendars."""

import re
from collections.abc import AsyncIterator
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from event_engine.models import RawEvent
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register

# Maximum pages to fetch to prevent runaway scraping
MAX_PAGES = 20
# Minimum events per page; if fewer, assume we've reached the end
MIN_EVENTS_PER_PAGE = 1


@register
class LibraryMarketScraper(BaseScraper):
    """Scrapes events from LibraryMarket (librarycalendar.com) library calendars.

    LibraryMarket is a Drupal-based platform used by many US public library
    systems. Events are listed at {base_url}/events/upcoming with pagination
    via ?page=N (0-indexed). Each page contains ~15-25 event cards as
    <article class="event-card event-card--sparse"> elements.

    Supports both Buncombe County (Asheville) and Cumberland County
    (Fayetteville) library systems, and should work with any LibraryMarket
    site using the same template.
    """

    platform = "librarymarket"

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Scrape all upcoming events across paginated pages."""
        seen_ids: set[str] = set()

        for page in range(MAX_PAGES):
            url = self._build_url(page)
            self.log.info("fetching_page", url=url, page=page)

            try:
                response = await self.fetch(url)
                soup = BeautifulSoup(response.text, "lxml")
                articles = self._find_event_articles(soup)

                if len(articles) < MIN_EVENTS_PER_PAGE:
                    self.log.info("no_more_events", page=page)
                    break

                page_count = 0
                for article in articles:
                    event = self._parse_article(article)
                    if event is None:
                        continue
                    if event.external_id in seen_ids:
                        continue
                    seen_ids.add(event.external_id)
                    page_count += 1
                    yield event

                self.log.info("page_scraped", page=page, events=page_count)

            except Exception as exc:
                self.log.warning("page_fetch_failed", page=page, url=url, error=str(exc))
                break

    def _build_url(self, page: int) -> str:
        """Build the URL for a specific page of upcoming events."""
        return f"{self.source.base_url}/events/upcoming?page={page}"

    def _find_event_articles(self, soup: BeautifulSoup) -> list[Tag]:
        """Find all event article elements on the page.

        LibraryMarket renders events as <article class="event-card event-card--sparse">
        elements. Featured events at the top use a different structure
        (lc-featured-event) and are also present in the main list, so we
        only parse the sparse cards.
        """
        return soup.select("article.event-card.event-card--sparse")

    def _parse_article(self, article: Tag) -> RawEvent | None:
        """Parse a single event article into a RawEvent."""
        # Find the event link with title
        link = article.select_one("h3.lc-event__title a.lc-event__link")
        if not link:
            return None

        href = link.get("href", "") or ""
        if isinstance(href, list):
            href = href[0]
        href = str(href)

        # Extract external ID from URL slug (e.g., /event/origami-bloom-21844 -> origami-bloom-21844)
        external_id = self._extract_slug_id(href)
        if not external_id:
            return None

        title = link.get_text(strip=True)
        if not title:
            return None

        # Build full source URL
        origin = self._get_origin()
        source_url = f"{origin}{href}" if href.startswith("/") else href

        # Extract date and time from aria-label and time div
        raw_start, raw_end = self._extract_datetime(article, link)

        # Extract branch/location
        location_name = self._extract_branch(article)

        # Extract age groups
        age_text = self._extract_age_groups(article)

        # Extract program types (categories)
        categories = self._extract_program_types(article)

        # Extract description
        description = self._extract_description(article)

        # Check if cancelled
        if "moderation-state--cancelled" in (article.get("class", []) or []):
            return None
        article_classes = article.get("class", [])
        if isinstance(article_classes, list) and "moderation-state--cancelled" in article_classes:
            return None

        return RawEvent(
            source_id=self.source.id,
            external_id=external_id,
            title=title,
            raw_start=raw_start,
            raw_end=raw_end,
            raw_location=location_name,
            raw_address=self.source.location.address or "",
            raw_categories=categories,
            raw_age_text=age_text,
            raw_cost_text="Free",
            description=description,
            source_url=source_url,
            is_recurring=False,
        )

    def _extract_slug_id(self, href: str) -> str:
        """Extract the event slug from the URL path.

        e.g., /event/origami-bloom-21844 -> origami-bloom-21844
        or https://buncombe.librarycalendar.com/event/origami-bloom-21844 -> origami-bloom-21844
        """
        try:
            parsed = urlparse(href)
            path = parsed.path.rstrip("/")
            if "/event/" in path:
                return path.split("/event/")[-1]
            return ""
        except Exception:
            return ""

    def _get_origin(self) -> str:
        """Return the scheme+host of base_url."""
        parsed = urlparse(self.source.base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def _extract_datetime(self, article: Tag, link: Tag) -> tuple[str, str]:
        """Extract start and end datetime strings from the article.

        Uses the aria-label on the link for the date, and the time div
        for the time range. Falls back to the date icon spans if needed.

        Returns (raw_start, raw_end) as human-readable strings.
        """
        raw_start = ""
        raw_end = ""

        # Strategy 1: Parse aria-label which contains full date
        # e.g., 'View more about "Title" on Saturday, March 28, 2026 @ 10:00am'
        aria = link.get("aria-label", "") or ""
        if isinstance(aria, list):
            aria = aria[0]
        aria = str(aria)

        date_from_aria = ""
        aria_match = re.search(r"on\s+\w+,\s+(\w+\s+\d{1,2},\s+\d{4})", aria)
        if aria_match:
            date_from_aria = aria_match.group(1)  # e.g., "March 28, 2026"

        # Strategy 2: Get time from the time info div
        time_div = article.select_one(
            ".lc-event__event-details--upcoming .lc-event-info-item--time"
        )
        if time_div:
            time_text = time_div.get_text(strip=True).replace("\xa0", " ")

            if "All Day" in time_text:
                # All-day event — use date from aria-label
                if date_from_aria:
                    raw_start = date_from_aria
                return (raw_start, raw_end)

            # Parse time range: "10:00am–11:30am" or "10:00am - 11:30am"
            # The dash can be an en-dash or hyphen
            time_range = re.match(
                r"(\d{1,2}:\d{2}\s*[APap][Mm])\s*[\u2013\-–]\s*(\d{1,2}:\d{2}\s*[APap][Mm])",
                time_text,
            )
            if time_range and date_from_aria:
                start_time = time_range.group(1).strip()
                end_time = time_range.group(2).strip()
                raw_start = f"{date_from_aria} {start_time}"
                raw_end = f"{date_from_aria} {end_time}"
                return (raw_start, raw_end)

            # Single time
            single_time = re.match(r"(\d{1,2}:\d{2}\s*[APap][Mm])", time_text)
            if single_time and date_from_aria:
                raw_start = f"{date_from_aria} {single_time.group(1).strip()}"
                return (raw_start, raw_end)

        # Fallback: use date from aria-label without time
        if date_from_aria:
            raw_start = date_from_aria

        # Fallback 2: Reconstruct from date icon spans
        if not raw_start:
            raw_start = self._extract_date_from_icon(article)

        return (raw_start, raw_end)

    def _extract_date_from_icon(self, article: Tag) -> str:
        """Extract date from the lc-date-icon spans as a fallback.

        The date icon contains:
          <span class="lc-date-icon__item--month">Mar</span>
          <span class="lc-date-icon__item--day">28</span>
          <span class="lc-date-icon__item--year">2026</span>
        """
        icon = article.select_one(
            ".lc-event__event-details--upcoming .lc-date-icon"
        )
        if not icon:
            return ""

        month_el = icon.select_one(".lc-date-icon__item--month")
        day_el = icon.select_one(".lc-date-icon__item--day")
        year_el = icon.select_one(".lc-date-icon__item--year")

        if month_el and day_el and year_el:
            month = month_el.get_text(strip=True)
            day = day_el.get_text(strip=True)
            year = year_el.get_text(strip=True)
            # day might be multi-day like "25 - 28", take the first
            day = re.match(r"\d+", day)
            if day:
                return f"{month} {day.group(0)}, {year}"

        return ""

    def _extract_branch(self, article: Tag) -> str:
        """Extract the library branch name from the expanded details."""
        # Look in the details panel
        branch_div = article.select_one(".lc-event__branch")
        if branch_div:
            # Remove the "Library Branch:" label
            text = branch_div.get_text(strip=True)
            text = re.sub(r"^Library Branch:\s*", "", text)
            if text:
                return text

        return self.source.location.name

    def _extract_age_groups(self, article: Tag) -> str:
        """Extract age group text from the article.

        Also checks the color-coded indicators in the compact view.
        """
        # From expanded details
        age_div = article.select_one(".lc-event__age-groups")
        if age_div:
            spans = age_div.select("span")
            if spans:
                return ", ".join(s.get_text(strip=True) for s in spans)

        # From compact color indicators
        colors_div = article.select_one(".lc-event-info__item--colors")
        if colors_div:
            text = colors_div.get_text(strip=True).replace("\xa0", " ")
            if text:
                return text

        return ""

    def _extract_program_types(self, article: Tag) -> list[str]:
        """Extract program type categories."""
        types_div = article.select_one(".lc-event__program-types")
        if types_div:
            spans = types_div.select("span")
            if spans:
                return [s.get_text(strip=True) for s in spans]
        return []

    def _extract_description(self, article: Tag) -> str:
        """Extract the event description/body text."""
        body_div = article.select_one(".lc-event__body")
        if body_div:
            return body_div.get_text(separator=" ", strip=True)[:500]
        return ""
