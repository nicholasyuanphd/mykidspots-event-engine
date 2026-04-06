"""MeilisearchTourismScraper — adapter for tourism CVB sites using Meilisearch search API."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx

from event_engine.models import RawEvent, SourceConfig
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register

_LIMIT = 50


@register
class MeilisearchTourismScraper(BaseScraper):
    """Scrapes events from tourism CVB sites that use Meilisearch as their search backend.

    Used by Charlotte's Got A Lot (charlottesgotalot.com) and similar CVB sites.

    The YAML config must provide these fields in ``selectors``:
    - ``search_url``: Base URL of the Meilisearch instance
    - ``index``: Meilisearch index name (e.g., ``cluster_events``)
    - ``api_key``: Public search-only API key embedded in the site HTML

    Paginates using ``offset`` until fewer results than ``limit`` are returned
    or all estimated hits are consumed. Deduplicates events by ``id`` across pages.
    """

    platform = "meilisearch"

    def __init__(self, source: SourceConfig, client: httpx.AsyncClient) -> None:
        super().__init__(source, client)
        # Mutable state for current POST request — set before each fetch_json call
        self._current_body: dict[str, Any] = {}
        self._current_headers: dict[str, str] = {}

    async def fetch_json(self, url: str, **kwargs: object) -> "dict[str, Any] | list[Any]":
        """Override to perform a POST request with JSON body instead of GET.

        The body and headers are set on the instance before calling this method
        so that tests can mock ``fetch_json`` while production code uses POST.
        """
        delay_s = self.source.request_delay_ms / 1000.0
        if delay_s > 0:
            import asyncio
            await asyncio.sleep(delay_s)
        response = await self.client.post(
            url,
            json=self._current_body,
            headers=self._current_headers,
        )
        response.raise_for_status()
        return response.json()  # type: ignore[no-any-return]

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Fetch events from the Meilisearch index and yield RawEvents."""
        search_url = self.source.selectors["search_url"]
        index = self.source.selectors["index"]
        api_key = self.source.selectors["api_key"]
        endpoint = f"{search_url}/indexes/{index}/search"

        today_unix = int(datetime.now(UTC).timestamp())
        seen_ids: set[str] = set()
        offset = 0

        self._current_headers = {
            "Authorization": f"Bearer {api_key}",
            "Origin": self.source.base_url,
            "Referer": f"{self.source.base_url}/events/",
        }

        while True:
            self._current_body = {
                "limit": _LIMIT,
                "offset": offset,
                "filter": f"startDate > {today_unix}",
                "sort": ["startDate:asc"],
                "attributesToRetrieve": ["*"],
            }

            self.log.info("fetching_meilisearch_page", endpoint=endpoint, offset=offset)

            try:
                data = await self.fetch_json(endpoint)
            except Exception as exc:
                self.log.warning("meilisearch_fetch_failed", offset=offset, error=str(exc))
                break

            assert isinstance(data, dict)
            hits: list[dict[str, Any]] = data.get("hits", [])
            estimated_total: int = data.get("estimatedTotalHits", 0)

            for hit in hits:
                event_id = str(hit.get("id", ""))
                if not event_id or event_id in seen_ids:
                    continue
                seen_ids.add(event_id)
                yield self._parse_hit(hit)

            offset += len(hits)
            if len(hits) < _LIMIT or offset >= estimated_total:
                break

    def _parse_hit(self, hit: dict[str, Any]) -> RawEvent:
        """Map a Meilisearch hit to a RawEvent."""
        # Convert Unix second timestamps to ISO datetime strings
        start_ts = hit.get("startDate")
        end_ts = hit.get("endDate")
        raw_start = (
            datetime.fromtimestamp(start_ts, tz=UTC).isoformat() if start_ts else ""
        )
        raw_end = (
            datetime.fromtimestamp(end_ts, tz=UTC).isoformat() if end_ts else ""
        )

        # Build source URL from base_url + uri
        uri = hit.get("uri", "")
        source_url = f"{self.source.base_url}/{uri}".rstrip("/") if uri else hit.get("website", "")

        # Venue name from nested place dict
        place = hit.get("place") or {}
        raw_location = place.get("title", "") if isinstance(place, dict) else ""

        # Cost text
        is_free = hit.get("isFree", False)
        event_price = hit.get("eventPrice", 0)
        if is_free:
            raw_cost_text = "Free"
        elif event_price:
            raw_cost_text = str(event_price)
        else:
            raw_cost_text = ""

        return RawEvent(
            source_id=self.source.id,
            external_id=str(hit.get("id", "")),
            title=hit.get("title", ""),
            description=hit.get("description") or "",
            raw_start=raw_start,
            raw_end=raw_end,
            raw_location=raw_location,
            raw_address=hit.get("fullAddress", ""),
            source_url=source_url,
            raw_cost_text=raw_cost_text,
            raw_data={
                "latitude": hit.get("latitude"),
                "longitude": hit.get("longitude"),
            },
        )
