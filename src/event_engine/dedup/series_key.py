"""Series key computation for grouping pre-expanded recurring events."""

import hashlib


def compute_series_key(
    title: str,
    location_name: str,
    time_of_day: str | None,
) -> str | None:
    """Compute a stable series key for grouping pre-expanded recurring events.

    The series key is a 16-char hex prefix of a SHA-256 hash built from the
    normalised title, location, and time-of-day.  Events that share the same
    series key represent the same programme on different dates.

    Args:
        title: Event title (will be stripped and lowercased).
        location_name: Venue / location name (will be stripped and lowercased).
        time_of_day: HH:MM string, or None for all-day events.

    Returns:
        16-character hex string, or None for all-day events (time_of_day is None).
    """
    if not time_of_day:
        return None
    normalized = (
        f"{title.strip().lower()}|"
        f"{location_name.strip().lower()}|"
        f"{time_of_day}"
    )
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]
