"""Shared test fixtures."""

from decimal import Decimal
from pathlib import Path

import pytest

from event_engine.models import RawEvent, SourceConfig
from event_engine.models.source_config import LocationConfig


@pytest.fixture
def sample_source() -> SourceConfig:
    """A sample Wake County library source config."""
    return SourceConfig(
        id="wake-oberlin-library",
        name="Oberlin Regional Library",
        platform="wakegov",
        trust_level="verified",
        enabled=True,
        timezone="America/New_York",
        base_url="https://www.wake.gov/events",
        location_id="396",
        location=LocationConfig(
            name="Oberlin Regional Library",
            address="1101 Oberlin Rd, Raleigh, NC 27605",
            city="Raleigh",
            latitude=Decimal("35.7897"),
            longitude=Decimal("-78.6568"),
        ),
        request_delay_ms=1500,
        default_cost_type="free",
        category_overrides=["library"],
    )


@pytest.fixture
def sample_raw_event() -> RawEvent:
    """A sample raw event for testing normalization."""
    return RawEvent(
        source_id="wake-oberlin-library",
        external_id="baby-playdate-31",
        title="Baby Playdate",
        description="Join us for a Baby Playdate at the library!",
        raw_start="December 25, 2026 11:00 am",
        raw_end="December 25, 2026 12:00 pm",
        raw_location="Oberlin Regional Library",
        raw_categories=["Kids & Families"],
        raw_age_text="Young Children",
        raw_cost_text="Free",
        source_url="https://www.wake.gov/events/baby-playdate-31",
    )


@pytest.fixture
def fixtures_dir() -> Path:
    """Path to test fixtures directory."""
    return Path(__file__).parent / "fixtures"
