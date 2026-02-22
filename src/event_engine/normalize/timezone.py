"""Timezone normalization — parse fuzzy dates and convert to UTC."""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from dateutil import parser as dateutil_parser


def parse_datetime(raw: str, source_tz: str) -> datetime | None:
    """Parse a fuzzy datetime string and return a timezone-aware UTC datetime.

    Args:
        raw: Datetime string from any source (ISO 8601, human-readable, etc.)
        source_tz: IANA timezone name (e.g., 'America/New_York') to assume
                   if the raw string has no timezone info.

    Returns:
        Timezone-aware UTC datetime, or None if parsing fails.
    """
    if not raw or not raw.strip():
        return None

    try:
        dt = dateutil_parser.parse(raw, fuzzy=True)
    except (ValueError, OverflowError):
        return None

    # If no timezone info, assume source timezone
    if dt.tzinfo is None:
        tz = ZoneInfo(source_tz)
        dt = dt.replace(tzinfo=tz)

    # Convert to UTC
    return dt.astimezone(UTC)


def is_future(dt: datetime) -> bool:
    """Check if a datetime is in the future (UTC)."""
    return dt > datetime.now(UTC)
