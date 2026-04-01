"""Normalization pipeline — transform RawEvent into NormalizedEvent."""

import re

import structlog

from event_engine.blocklist import is_blocked, is_civic_noise
from event_engine.dedup.fingerprint import compute_fingerprint
from event_engine.dedup.series_key import compute_series_key
from event_engine.models import NormalizedEvent, RawEvent, SourceConfig
from event_engine.normalize.age_parser import parse_age_range
from event_engine.normalize.category_mapper import map_categories
from event_engine.normalize.cost_parser import parse_cost
from event_engine.normalize.timezone import is_future, parse_datetime

logger = structlog.get_logger()

# Map scraper platform to frontend source badge type
PLATFORM_SOURCE_MAP: dict[str, str] = {
    "wakegov": "pipeline_library",
    "libcal": "pipeline_library",
    "bibliocommons": "pipeline_library",
    "librarymarket": "pipeline_library",
    "ical": "pipeline_museum",
    "civicplus": "pipeline_parks",
    "civicengage": "pipeline_library",
    "the_events_calendar": "pipeline_parks",
    "generic_html": "auto-imported",
    "localist": "pipeline_museum",
    "simpleview_rest": "pipeline_tourism",
    "meilisearch": "pipeline_tourism",
}

# Keywords that indicate an event is NOT for kids/families
ADULT_ONLY_KEYWORDS = [
    "adult book club",
    "senior",
    "seniors",
    "staff meeting",
    "board meeting",
    "staff training",
    "volunteer orientation",
    "job fair",
    "resume",
    "career",
    "tax preparation",
    "tax help",
    "medicare",
    "social security",
    "retirement",
    "genealogy",  # adult-focused at most libraries
    "wine",
    "beer",
    "cocktail",
    "happy hour",
]

# Keywords that indicate an event IS kid/family relevant
KID_FAMILY_KEYWORDS = [
    "kids",
    "children",
    "child",
    "family",
    "families",
    "baby",
    "babies",
    "infant",
    "toddler",
    "preschool",
    "storytime",
    "story time",
    "teen",
    "tween",
    "youth",
    "young",
    "elementary",
    "school age",
    "lego",
    "stem",
    "steam",
    "craft",
    "crafts",
    "playdate",
    "play date",
    "puppet",
]


def normalize(
    raw: RawEvent,
    source: SourceConfig,
    ai_verdict: str | None = None,  # "yes" | "no" | "maybe" | None
) -> NormalizedEvent | None:
    """Transform a raw event into a normalized, DB-ready event.

    Returns None if the event fails any quality gate:
    - Not a future event
    - Not free (Phase 1 only imports free events)
    - Not kid/family relevant

    Args:
        raw: Raw event data from a scraper.
        source: Source configuration for context (timezone, location, etc.)
        ai_verdict: Optional AI classification result ("yes", "no", "maybe", or None).
            If "no", the event is immediately rejected.

    Returns:
        NormalizedEvent ready for DB upsert, or None if rejected.
    """
    log = logger.bind(source_id=raw.source_id, external_id=raw.external_id, title=raw.title)

    # --- AI verdict check (skip if AI explicitly says not kid-relevant) ---
    if ai_verdict == "no":
        log.debug("skipping_ai_rejected", title=raw.title)
        return None

    # --- Blocklist check (curator-rejected titles never re-import) ---
    if is_blocked(raw.title):
        log.debug("skipped_blocklist", title=raw.title)
        return None

    # --- Civic noise filter (government admin events — meetings, hearings, etc.) ---
    # Only applied to town government sources (CivicPlus) that require AI classification
    if source.ai_classification == "required" and is_civic_noise(raw.title):
        log.debug("skipped_civic_noise", title=raw.title, source_id=source.id)
        return None

    # --- Timezone normalization ---
    start_dt = parse_datetime(raw.raw_start, source.timezone)
    if not start_dt:
        log.debug("skipped_no_start_datetime")
        return None

    end_dt = parse_datetime(raw.raw_end, source.timezone) if raw.raw_end else None

    # --- Quality gate: future events only ---
    if not raw.is_recurring and not is_future(start_dt):
        log.debug("skipped_past_event")
        return None

    # --- Cost parsing ---
    cost_type, cost_amount = parse_cost(raw.raw_cost_text or source.default_cost_type)

    # --- Quality gate: free events only (Phase 1) ---
    if cost_type != "free":
        log.debug("skipped_not_free", cost_type=cost_type)
        return None

    # --- Quality gate: kid/family relevance ---
    if not _is_kid_relevant(raw.title, raw.description, raw.raw_age_text, raw.raw_categories):
        log.debug("skipped_not_kid_relevant")
        return None

    # --- Age parsing ---
    age_min, age_max = parse_age_range(raw.raw_age_text, raw.title, raw.description)

    # --- Category mapping ---
    categories = map_categories(
        raw.raw_categories,
        raw.title,
        source.category_overrides,
    )

    # --- Location (prefer per-event coordinates from scraper, fall back to source config) ---
    location_name = " ".join((raw.raw_location or source.location.name).split())
    address = " ".join((raw.raw_address or source.location.address).split())
    # Per-event city (e.g., Localist geo.city) or source default
    raw_city = raw.raw_data.get("geo_city") if raw.raw_data else None
    city = raw_city or source.location.city
    # Per-event coordinates (e.g., BiblioCommons bc:location, TEC venue data)
    raw_lat = raw.raw_data.get("latitude") if raw.raw_data else None
    raw_lng = raw.raw_data.get("longitude") if raw.raw_data else None
    try:
        latitude = float(raw_lat) if raw_lat else source.location.latitude
        longitude = float(raw_lng) if raw_lng else source.location.longitude
    except (ValueError, TypeError):
        latitude = source.location.latitude
        longitude = source.location.longitude

    # --- Fingerprint ---
    fingerprint = compute_fingerprint(raw.source_id, raw.external_id)

    # --- Content policy: images (never use scraped images — copyright risk) ---
    image_urls: list[str] = []

    # --- Content policy: description ---
    if source.content_policy == "government":
        description = (_clean_description(raw.description) or None) if raw.description else None
    else:
        log.debug("description_dropped_copyright", content_policy=source.content_policy)
        description = None  # Drop copyrighted descriptions for nonprofit/commercial

    # --- Source URL ---
    source_url = raw.source_url or None

    # --- Registration URL ---
    registration_url = raw.registration_url or None

    # --- Status based on trust level ---
    status = "active" if source.trust_level == "verified" else "pending"

    # --- Source badge type (maps platform to frontend badge) ---
    source_type = PLATFORM_SOURCE_MAP.get(source.platform, "auto-imported")

    # --- Series key (groups pre-expanded recurring events) ---
    time_of_day = start_dt.strftime("%H:%M") if start_dt else None
    series_key = compute_series_key(
        title=raw.title.strip(),
        location_name=location_name or "",
        time_of_day=time_of_day,
    )

    return NormalizedEvent(
        source_fingerprint=fingerprint,
        title=raw.title.strip(),
        description=description,
        source_url=source_url,
        image_urls=image_urls,
        registration_url=registration_url,
        start_datetime=start_dt,
        end_datetime=end_dt,
        is_recurring=raw.is_recurring,
        location_name=location_name,
        address=address,
        city=city,
        latitude=latitude,
        longitude=longitude,
        timezone=source.timezone,
        category=categories,
        age_min=age_min,
        age_max=age_max,
        cost_type=cost_type,
        cost_amount=cost_amount,
        series_key=series_key,
        source=source_type,
        status=status,
        visibility="public",
    )


def _is_kid_relevant(
    title: str,
    description: str | None,
    age_text: str,
    categories: list[str],
) -> bool:
    """Check if an event is relevant for kids/families.

    Uses a combination of:
    1. Explicit adult-only keyword rejection
    2. Kid/family keyword detection
    3. Category-based inference
    4. Default: accept (library events are generally family-friendly)
    """
    combined = f"{title} {description or ''} {age_text}".lower()

    # Reject if adult-only keywords are present
    for keyword in ADULT_ONLY_KEYWORDS:
        if keyword in combined:
            # But allow if kid keywords are also present (e.g., "Family Genealogy")
            has_kid_keyword = any(k in combined for k in KID_FAMILY_KEYWORDS)
            if not has_kid_keyword:
                return False

    # Accept if kid/family keywords present
    for keyword in KID_FAMILY_KEYWORDS:
        if keyword in combined:
            return True

    # Accept if categories suggest kid relevance
    kid_categories = {"Kids & Families", "Young Children", "School-Age Children", "Teens"}
    for cat in categories:
        if cat in kid_categories:
            return True

    # For library events, default to accepting — most are family-friendly
    # The source config platform can be checked for this
    return True


def _clean_description(text: str) -> str:
    """Clean up description text."""
    # Remove excessive whitespace
    text = re.sub(r"\s+", " ", text).strip()
    # Remove HTML entities that survived parsing
    text = text.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return text
