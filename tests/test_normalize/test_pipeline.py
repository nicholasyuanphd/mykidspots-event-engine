"""Tests for the normalization pipeline."""

from event_engine.models import RawEvent, SourceConfig
from event_engine.normalize.pipeline import normalize


class TestNormalize:
    def test_happy_path(self, sample_raw_event: RawEvent, sample_source: SourceConfig) -> None:
        result = normalize(sample_raw_event, sample_source)
        assert result is not None
        assert result.title == "Baby Playdate"
        assert result.source == "pipeline_library"  # wakegov platform → pipeline_library
        assert result.status == "active"  # verified source
        assert result.city == "Raleigh"
        assert result.cost_type == "free"
        assert result.source_fingerprint is not None
        assert len(result.source_fingerprint) == 64  # SHA-256 hex

    def test_past_event_rejected(self, sample_source: SourceConfig) -> None:
        raw = RawEvent(
            source_id="test",
            external_id="past-event",
            title="Past Event",
            raw_start="January 1, 2020 10:00 am",
            raw_categories=["Kids & Families"],
        )
        result = normalize(raw, sample_source)
        assert result is None

    def test_paid_event_rejected(self, sample_source: SourceConfig) -> None:
        raw = RawEvent(
            source_id="test",
            external_id="paid-event",
            title="Family Workshop",
            raw_start="December 25, 2026 10:00 am",
            raw_categories=["Kids & Families"],
            raw_cost_text="$10 per child",
        )
        result = normalize(raw, sample_source)
        assert result is None

    def test_adult_event_rejected(self, sample_source: SourceConfig) -> None:
        raw = RawEvent(
            source_id="test",
            external_id="adult-event",
            title="Adult Book Club",
            description="Monthly adult book discussion group",
            raw_start="December 25, 2026 10:00 am",
            raw_categories=["Libraries"],
        )
        result = normalize(raw, sample_source)
        assert result is None

    def test_no_start_datetime_rejected(self, sample_source: SourceConfig) -> None:
        raw = RawEvent(
            source_id="test",
            external_id="no-date",
            title="Family Event",
            raw_start="",
        )
        result = normalize(raw, sample_source)
        assert result is None

    def test_verified_source_active_status(
        self, sample_raw_event: RawEvent, sample_source: SourceConfig
    ) -> None:
        result = normalize(sample_raw_event, sample_source)
        assert result is not None
        assert result.status == "active"

    def test_new_source_pending_status(
        self, sample_raw_event: RawEvent, sample_source: SourceConfig
    ) -> None:
        sample_source.trust_level = "new"
        result = normalize(sample_raw_event, sample_source)
        assert result is not None
        assert result.status == "pending"

    def test_location_from_source_config(self, sample_source: SourceConfig) -> None:
        raw = RawEvent(
            source_id="wake-oberlin-library",
            external_id="event-1",
            title="Family Storytime",
            raw_start="December 25, 2026 10:00 am",
            raw_categories=["Kids & Families"],
        )
        result = normalize(raw, sample_source)
        assert result is not None
        assert result.location_name == "Oberlin Regional Library"
        assert result.address == "1101 Oberlin Rd, Raleigh, NC 27605"
        assert result.city == "Raleigh"

    def test_categories_include_source_overrides(
        self, sample_raw_event: RawEvent, sample_source: SourceConfig
    ) -> None:
        result = normalize(sample_raw_event, sample_source)
        assert result is not None
        assert "library" in result.category

    def test_recurring_event_not_rejected_for_past_start(self, sample_source: SourceConfig) -> None:
        raw = RawEvent(
            source_id="test",
            external_id="recurring-past-start",
            title="Weekly Family Storytime",
            raw_start="January 1, 2020 10:00 am",
            raw_categories=["Kids & Families"],
            is_recurring=True,
        )
        result = normalize(raw, sample_source)
        # Recurring events with past start dates should NOT be rejected
        assert result is not None

    # --- Content policy tests ---

    def test_government_source_keeps_description(
        self, sample_raw_event: RawEvent, sample_source: SourceConfig
    ) -> None:
        """Government sources (public domain) should preserve descriptions."""
        sample_source.content_policy = "government"
        result = normalize(sample_raw_event, sample_source)
        assert result is not None
        assert result.description == "Join us for a Baby Playdate at the library!"

    def test_nonprofit_source_drops_description(
        self, sample_raw_event: RawEvent, sample_source: SourceConfig
    ) -> None:
        """Nonprofit sources have copyrighted descriptions — must be dropped."""
        sample_source.content_policy = "nonprofit"
        result = normalize(sample_raw_event, sample_source)
        assert result is not None
        assert result.description is None

    def test_commercial_source_drops_description(
        self, sample_raw_event: RawEvent, sample_source: SourceConfig
    ) -> None:
        """Commercial sources have copyrighted descriptions — must be dropped."""
        sample_source.content_policy = "commercial"
        result = normalize(sample_raw_event, sample_source)
        assert result is not None
        assert result.description is None

    def test_image_urls_always_empty(
        self, sample_source: SourceConfig
    ) -> None:
        """Images are never passed through — copyright risk regardless of source type."""
        raw = RawEvent(
            source_id="wake-oberlin-library",
            external_id="event-with-image",
            title="Family Storytime",
            raw_start="December 25, 2026 10:00 am",
            raw_categories=["Kids & Families"],
            image_url="https://example.com/photo.jpg",
        )
        result = normalize(raw, sample_source)
        assert result is not None
        assert result.image_urls == []

    def test_platform_source_mapping_wakegov(
        self, sample_raw_event: RawEvent, sample_source: SourceConfig
    ) -> None:
        """wakegov platform maps to pipeline_library source badge."""
        sample_source.platform = "wakegov"
        result = normalize(sample_raw_event, sample_source)
        assert result is not None
        assert result.source == "pipeline_library"

    def test_platform_source_mapping_ical(
        self, sample_raw_event: RawEvent, sample_source: SourceConfig
    ) -> None:
        """ical platform maps to pipeline_museum source badge."""
        sample_source.platform = "ical"
        result = normalize(sample_raw_event, sample_source)
        assert result is not None
        assert result.source == "pipeline_museum"

    def test_platform_source_mapping_libcal(
        self, sample_raw_event: RawEvent, sample_source: SourceConfig
    ) -> None:
        """libcal platform maps to pipeline_library source badge."""
        sample_source.platform = "libcal"
        result = normalize(sample_raw_event, sample_source)
        assert result is not None
        assert result.source == "pipeline_library"

    def test_platform_source_mapping_bibliocommons(
        self, sample_raw_event: RawEvent, sample_source: SourceConfig
    ) -> None:
        """bibliocommons platform maps to pipeline_library source badge."""
        sample_source.platform = "bibliocommons"
        result = normalize(sample_raw_event, sample_source)
        assert result is not None
        assert result.source == "pipeline_library"

    def test_platform_source_mapping_unknown(
        self, sample_raw_event: RawEvent, sample_source: SourceConfig
    ) -> None:
        """Unknown platforms fall back to auto-imported source badge."""
        sample_source.platform = "some_new_platform"
        result = normalize(sample_raw_event, sample_source)
        assert result is not None
        assert result.source == "auto-imported"

    # --- AI verdict tests ---

    def test_ai_verdict_no_causes_normalize_to_return_none(
        self, sample_raw_event: RawEvent, sample_source: SourceConfig
    ) -> None:
        """normalize() returns None immediately when ai_verdict is 'no'."""
        result = normalize(sample_raw_event, sample_source, ai_verdict="no")
        assert result is None

    def test_ai_verdict_yes_allows_event_through(
        self, sample_raw_event: RawEvent, sample_source: SourceConfig
    ) -> None:
        """normalize() continues normally when ai_verdict is 'yes'."""
        result = normalize(sample_raw_event, sample_source, ai_verdict="yes")
        assert result is not None
        assert result.title == "Baby Playdate"

    def test_ai_verdict_maybe_allows_event_through(
        self, sample_raw_event: RawEvent, sample_source: SourceConfig
    ) -> None:
        """normalize() continues normally when ai_verdict is 'maybe'."""
        result = normalize(sample_raw_event, sample_source, ai_verdict="maybe")
        assert result is not None

    def test_ai_verdict_none_allows_event_through(
        self, sample_raw_event: RawEvent, sample_source: SourceConfig
    ) -> None:
        """normalize() continues normally when ai_verdict is None (no classification)."""
        result = normalize(sample_raw_event, sample_source, ai_verdict=None)
        assert result is not None

    def test_ai_verdict_no_takes_priority_over_kid_relevant_title(
        self, sample_source: SourceConfig
    ) -> None:
        """ai_verdict='no' rejects event even if the title contains kid keywords."""
        raw = RawEvent(
            source_id="test",
            external_id="kids-event-ai-rejected",
            title="Kids Storytime",
            raw_start="December 25, 2026 10:00 am",
            raw_categories=["Kids & Families"],
        )
        result = normalize(raw, sample_source, ai_verdict="no")
        assert result is None


def test_simpleview_rest_platform_maps_to_tourism_badge():
    """simpleview_rest platform maps to pipeline_tourism source badge."""
    from event_engine.normalize.pipeline import PLATFORM_SOURCE_MAP
    assert PLATFORM_SOURCE_MAP["simpleview_rest"] == "pipeline_tourism"


def test_meilisearch_platform_maps_to_tourism_badge():
    """meilisearch platform maps to pipeline_tourism source badge."""
    from event_engine.normalize.pipeline import PLATFORM_SOURCE_MAP
    assert PLATFORM_SOURCE_MAP["meilisearch"] == "pipeline_tourism"


def test_normalize_handles_none_address_in_tourism_source():
    """normalize() does not crash when source.location.address is None (tourism configs)."""
    from decimal import Decimal
    from event_engine.models.source_config import LocationConfig

    source = SourceConfig(
        id="visitraleigh-events",
        name="Visit Raleigh Events",
        platform="simpleview_rest",
        trust_level="new",
        content_policy="commercial",
        enabled=True,
        timezone="America/New_York",
        base_url="https://www.visitraleigh.com",
        location_id="",
        location=LocationConfig(
            name="Raleigh",
            city="Raleigh",
            latitude=Decimal("35.7796"),
            longitude=Decimal("-78.6382"),
            # address intentionally omitted — tourism sources have no default address
        ),
        request_delay_ms=0,
        default_cost_type="free",
        ai_classification="required",
        category_overrides=["community"],
    )
    raw = RawEvent(
        source_id="visitraleigh-events",
        external_id="holiday-festival-2026",
        title="Raleigh Kids Holiday Festival",
        raw_start="December 20, 2026 10:00 am",
        raw_location="Moore Square",
        raw_address="",  # scraper found no address — empty string + None source address = crash pre-fix
        raw_categories=["Family Fun"],
        raw_cost_text="Free",
    )
    result = normalize(raw, source)
    assert result is not None
    assert result.address == ""  # gracefully empty, not a crash
