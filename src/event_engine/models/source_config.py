"""SourceConfig — YAML-based source configuration model."""

from decimal import Decimal
from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field


class LocationConfig(BaseModel):
    """Fixed location data for a source."""

    name: str
    address: str | None = None
    city: str
    latitude: Decimal | None = None
    longitude: Decimal | None = None


TrustLevel = Literal["verified", "new"]
ContentPolicy = Literal["government", "nonprofit", "commercial"]
AIClassificationMode = Literal["required", "optional", "skip"]


class SourceConfig(BaseModel):
    """Configuration for a single event source."""

    id: str
    """Unique identifier (e.g., 'wake-oberlin-library')."""

    name: str
    """Human-readable name (e.g., 'Oberlin Regional Library')."""

    platform: str
    """Scraper adapter to use (e.g., 'wakegov', 'libcal', 'ical')."""

    trust_level: TrustLevel = "new"
    """'verified' → auto-publish as active. 'new' → insert as pending for review."""

    content_policy: ContentPolicy = "commercial"
    """Content copyright policy: 'government' keeps descriptions (public domain),
    'nonprofit' and 'commercial' drop descriptions (copyrighted content).
    Default is 'commercial' (deny-by-default) — explicitly set 'government' for public domain sources."""

    enabled: bool = True
    """Whether this source should be scraped."""

    timezone: str = "America/New_York"
    """Timezone for events from this source."""

    base_url: str
    """Base URL for the event source."""

    location_id: str = ""
    """Platform-specific location/calendar identifier."""

    location: LocationConfig
    """Default location data for events from this source."""

    request_delay_ms: int = 1000
    """Minimum delay between HTTP requests to this source (ms)."""

    default_cost_type: str = "free"
    """Default cost type for events from this source."""

    category_overrides: list[str] = Field(default_factory=list)
    """Categories to always apply to events from this source."""

    department_id: str = "25"
    """WakeGov department filter (field_department_target_id).
    Default '25' = Libraries. Use '195' for Parks, Recreation and Open Space."""

    ai_classification: AIClassificationMode = "skip"
    """AI classification mode:
    'required' — classify all events; reject 'no', queue 'maybe', publish 'yes'
    'optional' — classify if ANTHROPIC_API_KEY is set, otherwise skip
    'skip'     — no AI classification (default; use for clean sources like libraries)
    """

    # CSS selectors for generic_html scraper
    selectors: dict[str, str] = Field(default_factory=dict)
    """CSS selectors for generic HTML scraping (event_list, title, date, etc.)."""


class SourceFile(BaseModel):
    """Top-level YAML file containing multiple source configs."""

    sources: list[SourceConfig]


def load_sources(sources_dir: Path, source_filter: str | None = None) -> list[SourceConfig]:
    """Load all enabled source configs from YAML files in a directory.

    Args:
        sources_dir: Path to directory containing .yml source files.
        source_filter: Optional glob pattern to filter source IDs (e.g., 'wake_*').

    Returns:
        List of enabled SourceConfig objects.
    """
    import fnmatch

    sources: list[SourceConfig] = []

    for yml_path in sorted(sources_dir.glob("*.yml")):
        if yml_path.name.startswith("_"):
            continue  # Skip template files

        with open(yml_path) as f:
            data = yaml.safe_load(f)

        if not data or "sources" not in data:
            continue

        source_file = SourceFile.model_validate(data)
        for source in source_file.sources:
            if not source.enabled:
                continue
            if source_filter and not fnmatch.fnmatch(source.id, source_filter):
                continue
            sources.append(source)

    return sources
