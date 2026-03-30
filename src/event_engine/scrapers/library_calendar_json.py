"""LibraryCalendarJsonScraper — adapter for Drupal library_calendar JSON feed API.

Used by public libraries running the library_calendar Drupal module, which exposes
a clean JSON feed at {base_url}/events/feed/json. This is distinct from the
LibraryMarket HTML scraper — it uses the JSON API directly for cleaner data.

Known users:
- Chapel Hill Public Library (chapelhillpubliclibrary.org)
- Orange County Public Library (orangecountync.librarycalendar.com)

The feed returns all upcoming events in a single request when given
?current_date=YYYY-MM-DD. Events include title, dates, age groups, program
types, branch, and description — all structured as native JSON fields.
"""

import re
from collections.abc import AsyncIterator
from datetime import date, datetime, timezone

from event_engine.models import RawEvent
from event_engine.scrapers.base import BaseScraper
from event_engine.scrapers.registry import register


def _strip_html(text: str) -> str:
    """Remove HTML tags from a string."""
    if not text:
        return ""
    return re.sub(r"<[^>]+>", " ", text).strip()


def _extract_dict_values(d: dict | list | None) -> list[str]:
    """Extract values from a {id: label} dict, ignoring keys."""
    if not d or not isinstance(d, dict):
        return []
    return [str(v) for v in d.values() if v]


@register
class LibraryCalendarJsonScraper(BaseScraper):
    """Scrapes events from the library_calendar Drupal module JSON feed.

    The JSON feed endpoint (/events/feed/json?current_date=YYYY-MM-DD) returns
    all upcoming events as a JSON array. Each event has structured fields for
    title, dates (in source timezone), age groups, program types, branch, and
    description.

    Configuration:
    - base_url: The library website base URL (e.g., https://chapelhillpubliclibrary.org)
    - timezone: IANA timezone for the library (default: America/New_York)

    The scraper fetches events from today forward in a single request.
    Cancelled events (moderation_state == 'cancelled') are skipped.
    """

    platform = "library_calendar_json"

    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Fetch all upcoming events from the JSON feed.

        If selectors["branch_filter"] is set, only events at that branch
        (case-insensitive substring match on the branch label) are yielded.
        This allows one source config per branch when the JSON feed returns
        events for all branches.
        """
        today = date.today().isoformat()
        base = self.source.base_url.rstrip("/")
        url = f"{base}/events/feed/json?current_date={today}"
        branch_filter = self.source.selectors.get("branch_filter", "").strip().lower()

        self.log.info("fetching_library_calendar_json", url=url, branch_filter=branch_filter or "all")

        try:
            data = await self.fetch_json(url)
        except Exception as exc:
            self.log.error("fetch_failed", url=url, error=str(exc))
            return

        if not isinstance(data, list):
            self.log.warning("unexpected_response_type", type=type(data).__name__)
            return

        self.log.info("events_fetched", count=len(data))

        seen_ids: set[str] = set()
        for item in data:
            # Apply branch filter if configured
            if branch_filter:
                branch_labels = _extract_dict_values(item.get("branch"))
                if not any(branch_filter in bl.lower() for bl in branch_labels):
                    continue

            event = self._parse_event(item)
            if event is None:
                continue
            if event.external_id in seen_ids:
                continue
            seen_ids.add(event.external_id)
            yield event

    def _parse_event(self, item: dict) -> RawEvent | None:
        """Parse a single JSON event into a RawEvent."""
        # Skip cancelled events
        if item.get("moderation_state") == "cancelled":
            return None

        # Skip unpublished events
        if not item.get("published", True):
            return None

        title = (item.get("title") or "").strip()
        if not title:
            return None

        # Skip events with "NO " prefix (cancellation notices like "NO Babytime at Southern")
        if title.upper().startswith("NO "):
            return None

        external_id = str(item.get("id") or "").strip()
        if not external_id:
            return None

        # Dates are in "YYYY-MM-DD HH:MM:SS" format, in source timezone
        raw_start = item.get("start_date") or ""
        raw_end = item.get("end_date") or ""

        # Skip all-day / midnight-only events (no meaningful time)
        if raw_start.endswith("00:00:00") and raw_end.endswith("00:00:00"):
            # Multi-day spans with 00:00 start are usually all-day admin entries
            # Only skip if it's a single-day zero-duration event
            if raw_start == raw_end:
                return None

        # Source URL
        source_url = item.get("url") or ""

        # Age groups — dict of {id: label}
        age_groups = _extract_dict_values(item.get("age_group"))
        raw_age_text = ", ".join(age_groups) if age_groups else ""

        # Program types — use as raw categories
        program_types = _extract_dict_values(item.get("program_type"))
        # Exclude generic "Recurring" tag — not useful as a category
        categories = [p for p in program_types if p != "Recurring"]

        # Branch — prefer the source's configured location name, but note the branch
        branch_labels = _extract_dict_values(item.get("branch"))
        branch_name = branch_labels[0] if branch_labels else self.source.location.name

        # Skip online-only events
        if branch_name == "Online Event":
            return None

        # Description — prefer description field, fall back to program_description
        description_raw = item.get("description") or ""
        if not description_raw:
            prog_desc = item.get("program_description") or {}
            if isinstance(prog_desc, dict):
                description_raw = " ".join(prog_desc.values())
            elif isinstance(prog_desc, str):
                description_raw = prog_desc
        description = _strip_html(description_raw)[:1000]

        return RawEvent(
            source_id=self.source.id,
            external_id=external_id,
            title=title,
            description=description,
            raw_start=raw_start,
            raw_end=raw_end,
            raw_location=branch_name,
            raw_address=self.source.location.address or "",
            raw_categories=categories,
            raw_age_text=raw_age_text,
            raw_cost_text="Free",
            source_url=source_url,
            is_recurring="Recurring" in program_types,
            raw_data=item,
        )
