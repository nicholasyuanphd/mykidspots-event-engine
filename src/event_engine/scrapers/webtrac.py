"""WebTracScraper — adapter for Vermont Systems WebTrac activity registration.

WebTrac (by Vermont Systems) is a parks & recreation registration system used by
hundreds of municipalities. It runs on myvscloud.com and serves activities,
facility rentals, and memberships.

The search interface is at:
    {subdomain}.myvscloud.com/webtrac/web/search.html?module=AR&display=Detail

Key challenges:
  1. The entire myvscloud.com domain is behind Cloudflare bot protection (blocks
     curl, httpx, and headless browsers).
  2. There is no public JSON API — results are server-rendered HTML tables.
  3. A CSRF token is required for every request.

Solution: Playwright with headed (non-headless) Chromium, which passes Cloudflare
fingerprinting. Activities are extracted from the HTML result tables.

Each activity section row contains:
  - Activity # (unique ID like "APFALLFLAGFOOTBALL8-015")
  - Description (e.g., "Fall NFL Flag Football - 8-10yo (Baileywick Park)")
  - Dates (e.g., "08/10/2026 -11/14/2026")
  - Times (e.g., "7:30 pm - 9:00 pm")
  - Days (e.g., "M, Tu, W, Th, F")
  - Location (venue name)
  - Ages (e.g., "8-10.99")
  - Cost (e.g., "$65.00/$80.00" = resident/non-resident)
  - Detail URL (iteminfo.html?Module=AR&FMID=...)

Requires: playwright[chromium] optional dependency.
"""

import asyncio
import re
from collections.abc import AsyncIterator
from urllib.parse import urlencode

from event_engine.models import RawEvent
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register

# Age categories to scrape. These cover the kid-relevant segments.
# Each WebTrac instance may use different category names.
# Raleigh: "1Preschool", "2Youth", "3Teen", "6Family"
# Asheville: "TODDLER", "YOUTH", "TEEN", "FAMILY"
# The source config's `category_overrides` field can specify which categories to query.

# Map of common WebTrac category aliases
_DEFAULT_CATEGORIES = ["youth", "family", "teen", "preschool", "toddler"]


@register
class WebTracScraper(BaseScraper):
    """Scrapes activities from Vermont Systems WebTrac (myvscloud.com).

    Uses Playwright with headed Chromium to bypass Cloudflare protection.
    Extracts activities from server-rendered HTML search result tables.

    Source config requirements:
      - base_url: Full URL to the search page, e.g.
          "https://ncraleighweb.myvscloud.com/webtrac/web/search.html"
      - location_id: Comma-separated category values to search, e.g.
          "2Youth,6Family,1Preschool,3Teen" (Raleigh) or
          "YOUTH,FAMILY,TODDLER,TEEN" (Asheville)
        If empty, scrapes all categories individually.
    """

    platform = "webtrac"

    # Max pages per category to prevent infinite loops
    MAX_PAGES = 15
    # Results per page (WebTrac default is 20)
    RESULTS_PER_PAGE = 20

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Scrape activities from WebTrac using Playwright."""
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            self.log.error(
                "playwright_not_installed",
                msg="Install playwright: uv pip install 'playwright>=1.49' "
                "&& playwright install chromium",
            )
            return

        categories = self._get_categories()
        self.log.info("webtrac_scrape_start", categories=categories)

        seen_fmids: set[str] = set()
        event_count = 0

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(
                headless=False,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            context = await browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                viewport={"width": 1920, "height": 1080},
            )
            page = await context.new_page()
            await page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )

            try:
                for category in categories:
                    self.log.info("webtrac_scraping_category", category=category)

                    page_num = 1
                    while page_num <= self.MAX_PAGES:
                        url = self._build_search_url(category, page_num)
                        self.log.debug(
                            "webtrac_fetching_page",
                            category=category,
                            page=page_num,
                            url=url[:200],
                        )

                        try:
                            resp = await page.goto(
                                url, wait_until="networkidle", timeout=30000
                            )
                        except Exception as exc:
                            self.log.warning(
                                "webtrac_page_load_failed",
                                category=category,
                                page=page_num,
                                error=str(exc),
                            )
                            break

                        if not resp or resp.status != 200:
                            self.log.warning(
                                "webtrac_bad_status",
                                status=resp.status if resp else 0,
                                category=category,
                            )
                            break

                        # Check for Cloudflare block
                        title = await page.title()
                        if "cloudflare" in title.lower() or "blocked" in title.lower():
                            self.log.error(
                                "webtrac_cloudflare_blocked",
                                msg="Cloudflare is blocking requests. "
                                "Headed Chromium should bypass this.",
                            )
                            break

                        # Wait for results to render
                        await page.wait_for_timeout(1500)

                        # Extract total results count
                        total_text = await page.evaluate(
                            """() => {
                            const el = document.querySelector('.header__subtext');
                            return el ? el.textContent.trim() : '';
                        }"""
                        )

                        if not total_text:
                            self.log.debug(
                                "webtrac_no_results", category=category, page=page_num
                            )
                            break

                        total_match = re.search(r"of\s+(\d+)", total_text)
                        total = int(total_match.group(1)) if total_match else 0

                        self.log.info(
                            "webtrac_page_results",
                            category=category,
                            page=page_num,
                            total=total,
                            text=total_text,
                        )

                        # Extract activities from this page
                        activities = await self._extract_page_activities(page)

                        for activity in activities:
                            fmid = activity.get("fmid", "")
                            if not fmid or fmid in seen_fmids:
                                continue
                            seen_fmids.add(fmid)

                            raw_event = self._to_raw_event(activity)
                            if raw_event:
                                event_count += 1
                                yield raw_event

                        # Check if there are more pages
                        current_count = page_num * self.RESULTS_PER_PAGE
                        if current_count >= total:
                            break

                        page_num += 1

                        # Rate limit between pages
                        delay_s = self.source.request_delay_ms / 1000.0
                        if delay_s > 0:
                            await asyncio.sleep(delay_s)

            finally:
                await browser.close()

        self.log.info(
            "webtrac_scrape_complete",
            total_events=event_count,
            unique_fmids=len(seen_fmids),
        )

    def _get_categories(self) -> list[str]:
        """Get the list of categories to search.

        Uses location_id from source config if set (comma-separated),
        otherwise falls back to default kid-friendly categories.
        """
        if self.source.location_id:
            return [c.strip() for c in self.source.location_id.split(",") if c.strip()]
        return list(_DEFAULT_CATEGORIES)

    def _build_search_url(self, category: str, page: int) -> str:
        """Build the WebTrac search URL with parameters."""
        base = self.source.base_url.rstrip("/")
        # Ensure we're pointing to search.html
        if not base.endswith("search.html"):
            base = base.rstrip("/") + "/search.html"

        params = {
            "Action": "Start",
            "SubAction": "",
            "module": "AR",
            "display": "Detail",
            "category": category,
            "search": "yes",
            "arwebsearch_buttonsearch": "yes",
            "keyword": "",
            "keywordoption": "Match One",
            "showwithavailable": "No",
            "spotsavailable": "",
            "bydayonly": "No",
            "beginyear": "",
            "dayoption": "Any",
            "page": str(page),
        }

        return f"{base}?{urlencode(params)}"

    async def _extract_page_activities(self, page: object) -> list[dict]:
        """Extract activity data from the current search results page.

        Returns a list of dicts with keys: fmid, activity_number, description,
        dates, times, days, location, ages, cost, detail_url, group_title,
        group_description.
        """
        return await page.evaluate(  # type: ignore[union-attr]
            """() => {
            const results = [];

            // Get group headers (activity group names + descriptions)
            const containers = document.querySelectorAll('.result-content');

            for (const container of containers) {
                // Get group-level info from header
                const header = container.querySelector('.result-header');
                const groupTitle = header?.querySelector('h2')?.textContent?.trim() || '';
                const groupDesc = header?.querySelector('.result-header__description')
                    ?.textContent?.trim() || '';

                // Get individual section rows from the table
                const table = container.querySelector('table[id^="arwebsearch_output_table"]');
                if (!table) continue;

                const rows = table.querySelectorAll('tbody tr');
                for (const row of rows) {
                    const cells = row.querySelectorAll('td');
                    if (cells.length === 0) continue;

                    const activity = {
                        group_title: groupTitle,
                        group_description: groupDesc,
                    };

                    for (const cell of cells) {
                        const label = cell.getAttribute('data-title');
                        if (!label) continue;

                        if (label === 'Activity #') {
                            const link = cell.querySelector('a');
                            activity.activity_number = cell.textContent.trim();
                            if (link) {
                                activity.detail_url = link.href;
                                // Extract FMID from URL
                                const match = link.href.match(/FMID=(\\d+)/);
                                activity.fmid = match ? match[1] : '';
                            }
                        } else if (label === 'Description') {
                            activity.description = cell.textContent.trim();
                        } else if (label === 'Dates') {
                            activity.dates = cell.textContent.trim();
                        } else if (label === 'Times') {
                            activity.times = cell.textContent.trim();
                        } else if (label === 'Days') {
                            activity.days = cell.textContent.trim();
                        } else if (label === 'Location') {
                            activity.location = cell.textContent.trim();
                        } else if (label === 'Ages') {
                            activity.ages = cell.textContent.trim();
                        } else if (label === 'Cost') {
                            activity.cost = cell.textContent.trim();
                        } else if (label === 'Availability') {
                            activity.availability = cell.textContent.trim();
                        }
                    }

                    if (activity.fmid) results.push(activity);
                }
            }

            return results;
        }"""
        )

    def _to_raw_event(self, activity: dict) -> RawEvent | None:
        """Convert an extracted activity dict to a RawEvent."""
        fmid = activity.get("fmid", "")
        description = activity.get("description", "")
        group_title = activity.get("group_title", "")

        # Use description as title (it's more specific than group_title)
        # e.g., "Fall NFL Flag Football - 8-10yo (Baileywick Park)"
        title = description or group_title
        if not title or not fmid:
            return None

        # Parse dates: "08/10/2026 -11/14/2026" or "08/10/2026 -11/14/2026*"
        dates_text = activity.get("dates", "")
        raw_start = ""
        raw_end = ""
        if dates_text:
            # Remove asterisks (indicate custom dates)
            dates_clean = dates_text.replace("*", "").strip()
            date_parts = re.split(r"\s*-\s*", dates_clean, maxsplit=1)
            if date_parts:
                raw_start = date_parts[0].strip()
                if len(date_parts) > 1:
                    raw_end = date_parts[1].strip()

        # Combine date and time for raw_start/raw_end
        times_text = activity.get("times", "")
        start_time = ""
        end_time = ""
        if times_text:
            time_parts = re.split(r"\s*-\s*", times_text, maxsplit=1)
            if time_parts:
                start_time = time_parts[0].strip()
                if len(time_parts) > 1:
                    end_time = time_parts[1].strip()

        if raw_start and start_time:
            raw_start = f"{raw_start} {start_time}"
        if raw_end and end_time:
            raw_end = f"{raw_end} {end_time}"
        elif raw_start and end_time and not raw_end:
            # Single-day event: end time on same date
            raw_end = f"{raw_start.split()[0]} {end_time}" if " " in raw_start else end_time

        # Location
        location = activity.get("location", "") or self.source.location.name

        # Ages
        ages_text = activity.get("ages", "")
        raw_age_text = ""
        if ages_text:
            # Convert "8-10.99" to "Ages 8-10"
            age_match = re.match(r"([\d.]+)-([\d.]+)", ages_text)
            if age_match:
                low = age_match.group(1).split(".")[0]
                high = age_match.group(2).split(".")[0]
                raw_age_text = f"Ages {low}-{high}"
            else:
                raw_age_text = ages_text

        # Cost: "$65.00/$80.00" (resident/non-resident) or "$0.00"
        cost_text = activity.get("cost", "")
        raw_cost_text = ""
        if cost_text:
            # Extract first price (resident price)
            price_match = re.search(r"\$([\d.]+)", cost_text)
            if price_match:
                price = float(price_match.group(1))
                raw_cost_text = "Free" if price == 0 else cost_text
            else:
                raw_cost_text = cost_text

        # Determine if this is a recurring/multi-session activity
        days_text = activity.get("days", "")
        is_recurring = bool(days_text and raw_start != raw_end)

        # Recurrence text from days
        recurrence_text = ""
        if days_text and is_recurring:
            recurrence_text = f"Days: {days_text}"

        # Detail URL
        detail_url = activity.get("detail_url", "")
        # Add InterfaceParameter for share-friendly URL
        if detail_url and "InterfaceParameter" not in detail_url:
            share_url = f"{detail_url}&InterfaceParameter=WebTrac"
        else:
            share_url = detail_url

        # Use group description if available (richer than section description)
        full_description = activity.get("group_description", "")

        return RawEvent(
            source_id=self.source.id,
            external_id=fmid,
            title=title,
            description=full_description,
            raw_start=raw_start,
            raw_end=raw_end,
            raw_location=location,
            raw_address=self.source.location.address or "",
            raw_categories=self.source.category_overrides or [],
            raw_age_text=raw_age_text,
            raw_cost_text=raw_cost_text,
            source_url=share_url,
            is_recurring=is_recurring,
            recurrence_text=recurrence_text,
            raw_data=activity,
        )
