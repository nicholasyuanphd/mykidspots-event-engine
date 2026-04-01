"""SimplyviewRestScraper — adapter for Simpleview CMS REST API (v1 and v2)."""

import json
from collections.abc import AsyncIterator
from datetime import date, timedelta
from typing import Any
from urllib.parse import quote

from event_engine.models import RawEvent
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register

_LIMIT = 50
_WINDOWS = 3
_WINDOW_DAYS = 30


@register
class SimplyviewRestScraper(BaseScraper):
    """Scrapes events from Simpleview CMS REST API endpoints.

    Supports two API versions:
    - v1: Used by VisitRaleigh and similar CVBs. Query params encoded directly
      in the URL as filter[...] bracket notation.
    - v2: Used by Wilmington and similar CVBs. Query params encoded as a
      URL-encoded JSON blob passed as `json=` parameter.

    The version is read from ``source.selectors["rest_version"]`` (default: "v1").

    Scrapes 3 rolling 30-day windows (days 0–30, 30–60, 60–90) and deduplicates
    events by ``_id`` across all windows and pages.
    """

    platform = "simpleview_rest"

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Fetch the API token then scrape events across 3 rolling date windows."""
        token = await self._fetch_token()
        seen_ids: set[str] = set()
        today = date.today()

        for window in range(_WINDOWS):
            start = today + timedelta(days=window * _WINDOW_DAYS)
            end = today + timedelta(days=(window + 1) * _WINDOW_DAYS)

            skip = 0
            while True:
                url = self._build_url(start, end, token, skip)
                self.log.info("fetching_window", url=url, window=window, skip=skip)

                try:
                    data = await self.fetch_json(url)
                except Exception as exc:
                    self.log.warning(
                        "window_fetch_failed", window=window, skip=skip, error=str(exc)
                    )
                    break

                assert isinstance(data, dict)
                docs = data.get("docs", [])
                total = data.get("total", 0)

                for doc in docs:
                    event_id = str(doc.get("_id", ""))
                    if not event_id or event_id in seen_ids:
                        continue
                    seen_ids.add(event_id)
                    yield self._parse_doc(doc)

                skip += len(docs)
                if len(docs) < _LIMIT or skip >= total:
                    break

    async def _fetch_token(self) -> str:
        """Fetch the Simpleview auth token from the token endpoint."""
        token_url = f"{self.source.base_url}/plugins/core/get_simple_token/"
        response = await self.fetch(token_url)
        return str(response.text).strip()

    def _build_url(self, start: date, end: date, token: str, skip: int = 0) -> str:
        """Build the API URL for the given date range and pagination offset."""
        rest_version = self.source.selectors.get("rest_version", "v1")
        start_str = start.strftime("%Y-%m-%d")
        end_str = end.strftime("%Y-%m-%d")

        if rest_version == "v2":
            return self._build_v2_url(start_str, end_str, token, skip)
        return self._build_v1_url(start_str, end_str, token, skip)

    def _build_v1_url(self, start_str: str, end_str: str, token: str, skip: int) -> str:
        """Build a v1 REST API URL with bracket-notation query params."""
        base = f"{self.source.base_url}/includes/rest/plugins_events_events/find/"
        return (
            f"{base}?token={token}"
            f"&filter[dates.eventDate][$gte]={start_str}"
            f"&filter[dates.eventDate][$lte]={end_str}"
            f"&options[limit]={_LIMIT}"
            f"&options[skip]={skip}"
            f"&options[fields][dates]=0"
        )

    def _build_v2_url(self, start_str: str, end_str: str, token: str, skip: int) -> str:
        """Build a v2 REST API URL with JSON-encoded query params."""
        base = f"{self.source.base_url}/includes/rest_v2/plugins_events_events_by_date/find/"
        payload = {
            "filter": {"date": {"$gte": start_str, "$lte": end_str}},
            "options": {"limit": _LIMIT, "skip": skip},
        }
        encoded = quote(json.dumps(payload))
        return f"{base}?token={token}&json={encoded}"

    def _parse_doc(self, doc: dict[str, Any]) -> RawEvent:
        """Map a Simpleview API event document to a RawEvent."""
        address_parts = [
            doc.get("address1", ""),
            doc.get("city", ""),
            doc.get("state", ""),
            doc.get("zip", ""),
        ]
        # Build "123 Main St, Raleigh, NC 27601"
        street = address_parts[0]
        city = address_parts[1]
        state = address_parts[2]
        zip_code = address_parts[3]

        raw_address_parts: list[str] = []
        if street:
            raw_address_parts.append(street)
        if city:
            raw_address_parts.append(city)
        if state and zip_code:
            raw_address_parts.append(f"{state} {zip_code}")
        elif state:
            raw_address_parts.append(state)
        raw_address = ", ".join(raw_address_parts)

        categories = [
            c["catName"]
            for c in doc.get("categories", [])
            if isinstance(c, dict) and "catName" in c
        ]

        return RawEvent(
            source_id=self.source.id,
            external_id=str(doc.get("_id", "")),
            title=doc.get("title", ""),
            description=doc.get("description", ""),
            raw_start=doc.get("startDate", ""),
            raw_end=doc.get("endDate", ""),
            raw_location=doc.get("location", ""),
            raw_address=raw_address,
            raw_categories=categories,
            source_url=doc.get("linkUrl", ""),
        )
