"""Tests for series_key computation."""

import re

from event_engine.dedup.series_key import compute_series_key


class TestComputeSeriesKey:
    """Tests for compute_series_key()."""

    def test_same_program_same_key(self):
        """Identical title/location/time always produces the same key."""
        key1 = compute_series_key("Baby Storytime", "Holly Springs Library", "10:00")
        key2 = compute_series_key("Baby Storytime", "Holly Springs Library", "10:00")
        assert key1 == key2

    def test_different_time_different_key(self):
        """Different time-of-day produces a different key."""
        key_morning = compute_series_key("Baby Storytime", "Holly Springs Library", "10:00")
        key_afternoon = compute_series_key("Baby Storytime", "Holly Springs Library", "14:00")
        assert key_morning != key_afternoon

    def test_different_location_different_key(self):
        """Different location produces a different key."""
        key_holly = compute_series_key("Baby Storytime", "Holly Springs Library", "10:00")
        key_apex = compute_series_key("Baby Storytime", "Apex Library", "10:00")
        assert key_holly != key_apex

    def test_different_title_different_key(self):
        """Different title produces a different key."""
        key_baby = compute_series_key("Baby Storytime", "Holly Springs Library", "10:00")
        key_toddler = compute_series_key("Toddler Storytime", "Holly Springs Library", "10:00")
        assert key_baby != key_toddler

    def test_case_insensitive(self):
        """Title and location are case-insensitive."""
        key_lower = compute_series_key("baby storytime", "holly springs library", "10:00")
        key_upper = compute_series_key("BABY STORYTIME", "HOLLY SPRINGS LIBRARY", "10:00")
        key_mixed = compute_series_key("Baby Storytime", "Holly Springs Library", "10:00")
        assert key_lower == key_upper == key_mixed

    def test_whitespace_normalized(self):
        """Leading/trailing whitespace is stripped."""
        key_clean = compute_series_key("Baby Storytime", "Holly Springs Library", "10:00")
        key_padded = compute_series_key("  Baby Storytime  ", "  Holly Springs Library  ", "10:00")
        assert key_clean == key_padded

    def test_returns_16_char_hex(self):
        """Key is exactly 16 hex characters."""
        key = compute_series_key("Baby Storytime", "Holly Springs Library", "10:00")
        assert key is not None
        assert len(key) == 16
        assert re.fullmatch(r"[0-9a-f]{16}", key)

    def test_returns_none_for_all_day_events(self):
        """All-day events (time_of_day=None) return None."""
        key = compute_series_key("Summer Festival", "Downtown Park", None)
        assert key is None
