"""CivicEngageScraper — adapter for CivicEngage embedded calendar grids.

CivicEngage is the same CMS vendor as CivicPlus, but some sites use
embedded calendar components instead of the standard Calendar.aspx endpoint.

These embedded calendars display a month-grid at a page URL like:
    {base_url}/about-us/calendar-of-library-events
with event detail links following the pattern:
    /Home/Components/Calendar/Event/{event_id}/{grid_id}

The scraper:
  1. Fetches the calendar grid page for 3 rolling months
  2. Extracts all event links from the grid
  3. Fetches each event detail page to parse structured data (itemprop HTML)

NOTE: CivicEngage sites (Greensboro, Dare County) are behind Akamai CDN
which performs TLS fingerprinting. Python's httpx/urllib3 TLS stacks are
fingerprinted and blocked (403). We use curl subprocess as the HTTP backend
since curl's TLS fingerprint is accepted by Akamai.
"""

import asyncio
import re
from collections.abc import AsyncIterator
from datetime import date
from urllib.parse import urlparse

from bs4 import BeautifulSoup, Tag

from event_engine.models import RawEvent
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register

# Pattern to extract event ID and grid ID from CivicEngage event URLs
EVENT_URL_RE = re.compile(r"/Home/Components/Calendar/Event/(\d+)/(\d+)")

# Browser-like headers required by Akamai CDN
_CURL_HEADERS = [
    "-H",
    "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36",
    "-H",
    "Accept: text/html,application/xhtml+xml,application/xml"
    ";q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "-H",
    "Accept-Language: en-US,en;q=0.9",
    "-H",
    "Accept-Encoding: gzip, deflate, br",
    "-H",
    "Sec-Fetch-Dest: document",
    "-H",
    "Sec-Fetch-Mode: navigate",
    "-H",
    "Sec-Fetch-Site: none",
    "-H",
    "Sec-Fetch-User: ?1",
]


@register
class CivicEngageScraper(BaseScraper):
    """Scrapes events from CivicEngage embedded calendar grids.

    CivicEngage sites embed a month-grid calendar widget. Event links follow
    the pattern: /Home/Components/Calendar/Event/{event_id}/{grid_id}

    The scraper fetches 3 consecutive months of the calendar grid, collects
    all event URLs, then fetches each event detail page for full data.

    Uses curl subprocess for HTTP requests to bypass Akamai CDN TLS
    fingerprinting that blocks Python HTTP libraries.
    """

    platform = "civicengage"

    async def _curl_fetch(self, url: str) -> str:
        """Fetch a URL using curl subprocess to bypass Akamai TLS fingerprinting.

        Uses asyncio.create_subprocess_exec (no shell) with hardcoded curl
        arguments. The url parameter is passed as a positional arg to curl,
        never interpolated into a shell string.

        Returns the response body as a string.
        Raises RuntimeError on non-200 responses or curl failures.
        """
        # Rate limiting
        delay_s = self.source.request_delay_ms / 1000.0
        if delay_s > 0:
            await asyncio.sleep(delay_s)

        cmd = [
            "curl",
            "-s",  # silent
            "-L",  # follow redirects
            "--compressed",  # handle gzip/br
            "--max-time",
            "30",
            "-w",
            "\n%{http_code}",  # append status code
            *_CURL_HEADERS,
            url,
        ]

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            raise RuntimeError(
                f"curl failed (exit {proc.returncode}): {stderr.decode()[:200]}"
            )

        output = stdout.decode("utf-8", errors="replace")
        # Last line is the HTTP status code
        lines = output.rsplit("\n", 1)
        if len(lines) == 2:
            body, status_str = lines
            status = int(status_str.strip()) if status_str.strip().isdigit() else 0
        else:
            body = output
            status = 0

        if status != 200:
            raise RuntimeError(f"HTTP {status} for {url}")

        return body

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Scrape events from 3 months of the calendar grid."""
        seen_ids: set[str] = set()
        today = date.today()

        for month_offset in range(3):
            target_month = today.month + month_offset
            target_year = today.year
            while target_month > 12:
                target_month -= 12
                target_year += 1

            grid_url = self._build_month_url(target_month, target_year)
            self.log.info(
                "fetching_calendar_grid",
                url=grid_url,
                month=target_month,
                year=target_year,
            )

            try:
                html = await self._curl_fetch(grid_url)
                soup = BeautifulSoup(html, "lxml")
                event_links = self._extract_event_links(soup)
                self.log.info(
                    "found_event_links",
                    count=len(event_links),
                    month=target_month,
                    year=target_year,
                )

                for event_id, detail_url in event_links:
                    if event_id in seen_ids:
                        continue
                    seen_ids.add(event_id)

                    try:
                        event = await self._fetch_event_detail(event_id, detail_url)
                        if event is not None:
                            yield event
                    except Exception as exc:
                        self.log.warning(
                            "event_detail_failed",
                            event_id=event_id,
                            url=detail_url,
                            error=str(exc),
                        )
                        continue

            except Exception as exc:
                self.log.warning(
                    "grid_fetch_failed",
                    url=grid_url,
                    month=target_month,
                    year=target_year,
                    error=str(exc),
                )
                continue

    def _build_month_url(self, month: int, year: int) -> str:
        """Build the calendar grid URL for a specific month.

        CivicEngage uses URL segments like:
            {calendar_page}/-curm-{month}/-cury-{year}
        The base calendar page URL is set in source.base_url.
        """
        return f"{self.source.base_url}/-curm-{month}/-cury-{year}"

    def _extract_event_links(
        self, soup: BeautifulSoup
    ) -> list[tuple[str, str]]:
        """Extract (event_id, full_url) pairs from the calendar grid.

        If source.location_id is set (grid ID), only extracts events
        matching that grid. This filters out sidebar/widget links that
        reference a different calendar grid on the same page.
        """
        origin = self._get_origin()
        grid_id = self.source.location_id or ""
        results: list[tuple[str, str]] = []

        for link in soup.find_all("a", href=True):
            href = link.get("href", "") or ""
            if isinstance(href, list):
                href = href[0]
            href = str(href)

            match = EVENT_URL_RE.search(href)
            if not match:
                continue

            event_id = match.group(1)
            link_grid_id = match.group(2)

            # Filter by grid ID if configured
            if grid_id and link_grid_id != grid_id:
                continue

            # Skip backlist/redirect links (duplicates with query params)
            if "backlist=" in href or "curdate=" in href:
                continue

            full_url = (
                f"{origin}/Home/Components/Calendar/Event"
                f"/{event_id}/{link_grid_id}"
            )
            results.append((event_id, full_url))

        return results

    async def _fetch_event_detail(
        self, event_id: str, detail_url: str
    ) -> RawEvent | None:
        """Fetch and parse a single event detail page.

        CivicEngage detail pages use schema.org/data-vocabulary.org markup
        with itemprop attributes for structured event data.
        """
        html = await self._curl_fetch(detail_url)
        soup = BeautifulSoup(html, "lxml")

        # Find the event container (uses data-vocabulary.org/Event itemscope)
        event_div = soup.find(attrs={"itemscope": True})
        if not event_div or not isinstance(event_div, Tag):
            self.log.debug("no_event_container", url=detail_url)
            return None

        # Title: itemprop="summary"
        title_el = event_div.find(attrs={"itemprop": "summary"})
        title = title_el.get_text(strip=True) if title_el else ""
        if not title:
            return None

        # Subtitle (optional): h3.detail-subtitle
        subtitle_el = event_div.select_one("h3.detail-subtitle")
        subtitle = subtitle_el.get_text(strip=True) if subtitle_el else ""

        # Dates: itemprop="startDate" and itemprop="endDate" in <time> elements
        raw_start = ""
        raw_end = ""

        start_time = event_div.find("time", attrs={"itemprop": "startDate"})
        end_time = event_div.find("time", attrs={"itemprop": "endDate"})

        if start_time and isinstance(start_time, Tag):
            # Prefer datetime attribute (ISO format), fall back to text
            raw_start = str(start_time.get("datetime", "")) or start_time.get_text(
                strip=True
            )

        if end_time and isinstance(end_time, Tag):
            raw_end = str(end_time.get("datetime", "")) or end_time.get_text(
                strip=True
            )

        # If no itemprop times, fall back to the date label text
        if not raw_start:
            date_label = event_div.find(
                "span", class_="detail-list-label", string=re.compile(r"Date")
            )
            if date_label:
                date_value = date_label.find_next_sibling(
                    "span", class_="detail-list-value"
                )
                if date_value:
                    raw_start = date_value.get_text(strip=True)

        # Location: itemprop="name" inside itemprop="location"
        location_name = self.source.location.name
        location_scope = event_div.find(attrs={"itemprop": "location"})
        if location_scope and isinstance(location_scope, Tag):
            name_el = location_scope.find(attrs={"itemprop": "name"})
            if name_el:
                loc_text = name_el.get_text(strip=True)
                if loc_text:
                    location_name = loc_text

        # Address: itemprop="street-address", "locality", "region"
        raw_address = ""
        addr_scope = event_div.find(attrs={"itemprop": "address"})
        if addr_scope and isinstance(addr_scope, Tag):
            street = addr_scope.find(attrs={"itemprop": "street-address"})
            locality = addr_scope.find(attrs={"itemprop": "locality"})
            region = addr_scope.find(attrs={"itemprop": "region"})
            parts = []
            if street:
                parts.append(street.get_text(strip=True))
            if locality:
                loc_text = locality.get_text(strip=True)
                if region:
                    loc_text += f", {region.get_text(strip=True)}"
                parts.append(loc_text)
            raw_address = ", ".join(parts)

        # Description: itemprop="description"
        description = ""
        desc_el = event_div.find(attrs={"itemprop": "description"})
        if desc_el and isinstance(desc_el, Tag):
            description = desc_el.get_text(separator=" ", strip=True)[:500]

        # Introduction/summary (sometimes separate from description)
        intro = ""
        intro_label = event_div.find(
            "span",
            class_="detail-list-label",
            string=re.compile(r"Introduction"),
        )
        if intro_label:
            intro_value = intro_label.find_next_sibling(
                "span", class_="detail-list-value"
            )
            if intro_value:
                intro = intro_value.get_text(strip=True)

        # Use intro as description if description is empty
        if not description and intro:
            description = intro

        # Cost: custom field labeled "Cost"
        raw_cost = ""
        cost_label = event_div.find(
            "span",
            class_="custom_field_label",
            string=re.compile(r"Cost", re.I),
        )
        if cost_label:
            cost_value = cost_label.find_next_sibling(
                "span", class_="custom_field_field"
            )
            if cost_value:
                raw_cost = cost_value.get_text(strip=True)

        # Build full title with subtitle if present
        full_title = f"{title}: {subtitle}" if subtitle else title

        return RawEvent(
            source_id=self.source.id,
            external_id=event_id,
            title=full_title,
            description=description,
            raw_start=raw_start,
            raw_end=raw_end,
            raw_location=location_name,
            raw_address=raw_address or self.source.location.address or "",
            raw_cost_text=raw_cost,
            is_recurring=False,
            source_url=detail_url,
        )

    def _get_origin(self) -> str:
        """Return the scheme+host of base_url."""
        parsed = urlparse(self.source.base_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    async def fetch_description(self, detail_url: str) -> str | None:
        """Fetch description from an event detail page (for AI classification)."""
        try:
            html = await self._curl_fetch(detail_url)
            soup = BeautifulSoup(html, "lxml")
            desc_el = soup.find(attrs={"itemprop": "description"})
            if desc_el and isinstance(desc_el, Tag):
                return desc_el.get_text(separator=" ", strip=True)[:500]
            return None
        except Exception:
            self.log.warning("detail_fetch_failed", url=detail_url)
            return None
