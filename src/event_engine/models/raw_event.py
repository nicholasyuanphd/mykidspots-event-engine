"""RawEvent — pre-normalization event data from scrapers."""

from pydantic import BaseModel


class RawEvent(BaseModel):
    """Raw event data as extracted from a source, before normalization.

    Scrapers populate this with whatever the source provides. All fields
    are strings or basic types — parsing happens in the normalization pipeline.
    """

    source_id: str
    """Source config ID (e.g., 'wake-oberlin-library')."""

    external_id: str
    """Platform-specific event ID for deduplication."""

    title: str
    """Raw event title."""

    description: str = ""
    """Raw description/body text."""

    raw_start: str
    """Unparsed start datetime string from the source."""

    raw_end: str = ""
    """Unparsed end datetime string from the source."""

    raw_location: str = ""
    """Location/venue name from the source."""

    raw_address: str = ""
    """Street address from the source."""

    raw_categories: list[str] = []
    """Source-specific tags or categories."""

    raw_age_text: str = ""
    """Age range text (e.g., 'Ages 3-5', 'Preschool', 'All ages')."""

    raw_cost_text: str = ""
    """Cost text (e.g., 'Free', '$10 per child', 'Varies')."""

    source_url: str = ""
    """URL to the event on the original source website."""

    image_url: str = ""
    """URL to the event image, if available."""

    registration_url: str = ""
    """URL for event registration, if available."""

    is_recurring: bool = False
    """Whether the source indicates this is a recurring event."""

    recurrence_text: str = ""
    """Raw recurrence description (e.g., 'Every Tuesday', 'Weekly')."""

    raw_data: dict = {}  # noqa: RUF012
    """Full source payload preserved for debugging."""
