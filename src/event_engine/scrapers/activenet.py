"""ActiveNetScraper — adapter for ACTIVE Network / ActiveCommunities parks & rec portals.

ActiveNet (apm.activecommunities.com) is a widely used platform for US municipal
parks & recreation departments. The frontend is a React SPA that communicates
with a REST API on the same domain.

API details (reverse-engineered from the SPA JS bundle):
  - Session: GET the SPA page to obtain a JSESSIONID cookie and CSRF token
  - List:    POST {base}/rest/activities/list  (body = filter params)
  - Detail:  GET  {base}/rest/activity/detail/{activityId}
  - Pagination: sent via an HTTP header named `page_info` (JSON-encoded)
  - Page size is server-enforced at 20 records per page (cannot be overridden)
"""

import json
import re
from collections.abc import AsyncIterator

import httpx
import structlog

from event_engine.models import RawEvent, SourceConfig
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register

logger = structlog.get_logger()

# The server enforces this page size regardless of what we request
ACTIVENET_PAGE_SIZE = 20


@register
class ActiveNetScraper(BaseScraper):
    """Scrapes activities from an ActiveNet (activecommunities.com) portal.

    ActiveNet sites are React SPAs backed by a session-based REST API.
    The scraper:
      1. Loads the SPA page to obtain a JSESSIONID cookie + CSRF token
      2. POSTs to /rest/activities/list with pagination via the page_info header
      3. Optionally fetches /rest/activity/detail/{id} for center geo data

    Source config:
      - base_url: The SPA root, e.g. "https://anc.apm.activecommunities.com/mecklenburgparks"
      - location_id: (optional) not used for ActiveNet; center info comes from the API
    """

    platform = "activenet"

    # Safety cap — 100 pages * 20/page = 2000 activities max
    MAX_PAGES = 100
    # Fetch detail for each activity to get center lat/lng and full description
    FETCH_DETAIL = True
    # Max activities to fetch detail for (to control runtime)
    MAX_DETAIL_FETCHES = 500

    def __init__(self, source: SourceConfig, client: httpx.AsyncClient) -> None:
        super().__init__(source, client)
        self._csrf_token: str = ""
        self._session_cookies: httpx.Cookies = httpx.Cookies()
        # Cache center details to avoid redundant detail calls
        self._center_cache: dict[int, dict] = {}

    async def _init_session(self) -> None:
        """Load the SPA page to obtain session cookies and a CSRF token.

        The ActiveNet SPA embeds a CSRF token in the HTML as:
            window.__csrfToken = "uuid-here";
        The server also sets JSESSIONID and other cookies needed for API calls.
        """
        base = self.source.base_url.rstrip("/")
        url = f"{base}/activity/search?onlineSiteId=0"

        self.log.info("activenet_init_session", url=url)

        # Use the underlying client directly to capture cookies
        response = await self.client.get(
            url,
            follow_redirects=True,
            headers={"Accept": "text/html"},
        )
        response.raise_for_status()

        # Extract CSRF token from the HTML
        html = response.text
        csrf_match = re.search(r'__csrfToken\s*=\s*"([^"]+)"', html)
        if not csrf_match:
            self.log.error("activenet_no_csrf_token")
            msg = "Could not find CSRF token in ActiveNet page"
            raise RuntimeError(msg)

        self._csrf_token = csrf_match.group(1)

        # Capture all cookies from the response chain
        self._session_cookies = httpx.Cookies()
        for resp in [response] + list(response.history):
            for name, value in resp.cookies.items():
                self._session_cookies.set(name, value)

        self.log.info(
            "activenet_session_ready",
            csrf_token=self._csrf_token[:8] + "...",
            cookies=len(self._session_cookies),
        )

    def _api_headers(self, page_number: int = 1) -> dict[str, str]:
        """Build HTTP headers for an ActiveNet REST API call.

        The SPA sends pagination info in a custom `page_info` HTTP header
        as a JSON-encoded string.
        """
        return {
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "X-CSRF-TOKEN": self._csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{self.source.base_url.rstrip('/')}/activity/search",
            "page_info": json.dumps({
                "page_number": page_number,
                "total_records_per_page": ACTIVENET_PAGE_SIZE,
            }),
        }

    async def _fetch_activities_page(self, page: int) -> dict:
        """Fetch a single page of activities from the list endpoint."""
        base = self.source.base_url.rstrip("/")
        url = f"{base}/rest/activities/list"

        # Rate limiting
        delay_s = self.source.request_delay_ms / 1000.0
        if delay_s > 0:
            import asyncio
            await asyncio.sleep(delay_s)

        response = await self.client.post(
            url,
            headers=self._api_headers(page),
            cookies=self._session_cookies,
            json={},
        )
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def _fetch_activity_detail(self, activity_id: int) -> dict | None:
        """Fetch detailed info for one activity (center address, coordinates, etc.)."""
        base = self.source.base_url.rstrip("/")
        url = f"{base}/rest/activity/detail/{activity_id}"

        delay_s = self.source.request_delay_ms / 1000.0
        if delay_s > 0:
            import asyncio
            await asyncio.sleep(delay_s)

        try:
            response = await self.client.get(
                url,
                headers=self._api_headers(),
                cookies=self._session_cookies,
            )
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict) and data.get("headers", {}).get("response_code") == "0000":
                return data.get("body", {}).get("detail")  # type: ignore[no-any-return]
        except Exception:
            self.log.warning("activenet_detail_failed", activity_id=activity_id)
        return None

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Fetch all activities from the ActiveNet portal."""
        # Step 1: Initialize session
        await self._init_session()

        # Step 2: Paginate through activity list
        page = 0
        total_yielded = 0
        seen_ids: set[int] = set()
        detail_fetched = 0

        while page < self.MAX_PAGES:
            page += 1
            self.log.info("activenet_fetching_page", page=page)

            try:
                data = await self._fetch_activities_page(page)
            except Exception:
                self.log.warning("activenet_page_failed", page=page)
                break

            if not isinstance(data, dict):
                break

            response_code = data.get("headers", {}).get("response_code", "")
            if response_code != "0000":
                self.log.warning("activenet_api_error", code=response_code, page=page)
                break

            page_info = data.get("headers", {}).get("page_info", {})
            total_pages = page_info.get("total_page", 0)

            items = data.get("body", {}).get("activity_items", [])
            if not items:
                break

            for item in items:
                activity_id = item.get("id")
                if not activity_id or activity_id in seen_ids:
                    continue
                seen_ids.add(activity_id)

                # Optionally fetch detail for center coordinates
                detail = None
                if self.FETCH_DETAIL and detail_fetched < self.MAX_DETAIL_FETCHES:
                    detail = await self._fetch_activity_detail(activity_id)
                    detail_fetched += 1

                event = self._parse_activity(item, detail)
                if event:
                    total_yielded += 1
                    yield event

            # Stop if we've reached the last page
            if page >= total_pages:
                break

        self.log.info(
            "activenet_scrape_complete",
            total_events=total_yielded,
            pages=page,
            detail_fetches=detail_fetched,
        )

    def _parse_activity(self, item: dict, detail: dict | None = None) -> RawEvent | None:
        """Parse an activity item (+ optional detail) into a RawEvent."""
        activity_id = item.get("id")
        name = (item.get("name") or "").strip()

        if not activity_id or not name:
            return None

        # Build start/end datetime strings
        # List API: date_range_start/end = "YYYY-MM-DD", time_range = "H:MM AM - H:MM PM"
        date_start = item.get("date_range_start", "") or ""
        date_end = item.get("date_range_end", "") or ""
        time_range = item.get("time_range", "") or ""

        raw_start = date_start
        raw_end = date_end
        if time_range and date_start:
            # Parse "7:00 PM - 10:45 PM" into start and end times
            time_parts = time_range.split(" - ")
            if len(time_parts) == 2:
                raw_start = f"{date_start} {time_parts[0].strip()}"
                raw_end = f"{date_end} {time_parts[1].strip()}" if date_end else ""
            else:
                raw_start = f"{date_start} {time_range}"

        # Description — strip HTML tags
        description = item.get("desc", "") or ""
        if detail:
            catalog_desc = detail.get("catalog_description", "") or ""
            if catalog_desc:
                description = catalog_desc
        description = re.sub(r"<[^>]+>", " ", description).strip()
        description = re.sub(r"\s+", " ", description)

        # Location — prefer detail endpoint for address and coordinates
        location_name = (item.get("location", {}) or {}).get("label", "") or ""
        raw_address = ""
        if detail and detail.get("centers"):
            center = detail["centers"][0]
            location_name = center.get("name", location_name)
            address_parts = [
                center.get("address1", ""),
                center.get("city", ""),
                center.get("state", ""),
                center.get("zip_code", ""),
            ]
            raw_address = ", ".join(p for p in address_parts if p)

        # Categories
        raw_categories = []
        if detail:
            cat = detail.get("category", "")
            if cat:
                raw_categories.append(cat)
            sub_cat = detail.get("sub_category", "")
            if sub_cat:
                raw_categories.append(sub_cat)

        # Age text
        raw_age_text = item.get("age_description", "") or item.get("ages", "") or ""

        # Cost
        fee_info = item.get("fee", {}) or {}
        raw_cost_text = fee_info.get("label", "") if isinstance(fee_info, dict) else ""

        # Source URL
        source_url = item.get("detail_url", "") or ""

        # Registration URL
        enroll_info = item.get("enroll_now", {}) or {}
        registration_url = enroll_info.get("href", "") if isinstance(enroll_info, dict) else ""

        # Recurrence detection — activities spanning multiple sessions are recurring
        sessions = 0
        if detail and detail.get("other_info"):
            sessions = detail["other_info"].get("sessions", 0) or 0
        days_of_week = item.get("days_of_week", "") or ""
        is_recurring = sessions > 1 or bool(days_of_week)
        recurrence_text = ""
        if days_of_week and date_start and date_end and date_start != date_end:
            recurrence_text = f"Every {days_of_week} from {date_start} to {date_end}"

        # Preserve full data for debugging
        raw_data = dict(item)
        if detail:
            raw_data["_detail"] = detail

        return RawEvent(
            source_id=self.source.id,
            external_id=str(activity_id),
            title=name,
            description=description,
            raw_start=raw_start,
            raw_end=raw_end,
            raw_location=location_name,
            raw_address=raw_address,
            raw_categories=raw_categories,
            raw_age_text=raw_age_text,
            raw_cost_text=raw_cost_text,
            source_url=source_url,
            registration_url=registration_url,
            is_recurring=is_recurring,
            recurrence_text=recurrence_text,
            raw_data=raw_data,
        )
