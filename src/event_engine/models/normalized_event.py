"""NormalizedEvent — DB-ready event data after normalization."""

from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field

# Canonical categories matching the MyKidSpots events table
VALID_CATEGORIES = frozenset(
    {
        "storytime",
        "library",
        "reading",
        "museum",
        "indoor-play",
        "outdoor",
        "playground",
        "splash-pad",
        "swimming",
        "art",
        "arts-crafts",
        "music",
        "class",
        "sports",
        "festival",
        "family",
        "science",
        "stem",
        "crafts",
        "social",
        "seasonal",
        "farmers-market",
        "special-event",
        "nature",
        "dining",
    }
)

CostType = Literal["free", "paid", "varies"]
EventStatus = Literal["active", "pending"]
RecurrencePattern = Literal["daily", "weekly", "biweekly", "monthly"]


class NormalizedEvent(BaseModel):
    """Normalized event ready for database upsert.

    Maps 1:1 to columns in the MyKidSpots `events` table.
    All datetimes are timezone-aware UTC.
    """

    # Identity
    source_fingerprint: str
    """SHA-256 hash of source_id:external_id for deduplication."""

    # Content
    title: str
    description: str | None = None
    source_url: str | None = None
    image_urls: list[str] = Field(default_factory=list)
    registration_url: str | None = None

    # Date/time (all UTC)
    start_datetime: datetime
    end_datetime: datetime | None = None

    # Recurrence
    is_recurring: bool = False
    recurrence_pattern: RecurrencePattern | None = None
    recurrence_days_of_week: list[str] | None = None
    recurrence_end_date: str | None = None  # DATE string (YYYY-MM-DD)
    recurrence_interval: int = 1

    # Location
    location_name: str
    address: str | None = None
    city: str
    latitude: Decimal | None = None
    longitude: Decimal | None = None
    timezone: str = "America/New_York"

    # Classification
    category: list[str] = Field(default_factory=lambda: ["family"])
    age_min: int = 0
    age_max: int = 17

    # Cost
    cost_type: CostType = "free"
    cost_amount: Decimal | None = None
    cost_details: str | None = None

    # Series grouping (pre-expanded recurring events)
    series_key: str | None = None

    # Metadata
    source: str = "auto-imported"
    status: EventStatus = "active"
    visibility: str = "public"
