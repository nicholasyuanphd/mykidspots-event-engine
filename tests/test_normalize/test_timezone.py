"""Tests for timezone normalization."""

from datetime import UTC, datetime

from event_engine.normalize.timezone import is_future, parse_datetime


class TestParseDatetime:
    def test_iso_8601_with_tz(self) -> None:
        result = parse_datetime("2026-03-15T14:00:00-04:00", "America/New_York")
        assert result is not None
        assert result.tzinfo == UTC
        assert result.hour == 18  # 2pm ET (EDT) = 6pm UTC

    def test_human_readable_no_tz(self) -> None:
        result = parse_datetime("February 20, 2026 11:00 am", "America/New_York")
        assert result is not None
        assert result.tzinfo == UTC
        # 11am ET in winter (EST) = 4pm UTC
        assert result.hour == 16
        assert result.day == 20

    def test_date_only(self) -> None:
        result = parse_datetime("March 15, 2026", "America/New_York")
        assert result is not None
        assert result.month == 3
        assert result.day == 15

    def test_empty_string(self) -> None:
        assert parse_datetime("", "America/New_York") is None

    def test_whitespace_only(self) -> None:
        assert parse_datetime("   ", "America/New_York") is None

    def test_unparseable(self) -> None:
        assert parse_datetime("not a date", "America/New_York") is None

    def test_central_timezone(self) -> None:
        result = parse_datetime("2026-06-15 10:00 AM", "America/Chicago")
        assert result is not None
        # 10am CT (CDT, summer) = 3pm UTC
        assert result.hour == 15

    def test_pacific_timezone(self) -> None:
        result = parse_datetime("2026-01-15 10:00 AM", "America/Los_Angeles")
        assert result is not None
        # 10am PT (PST, winter) = 6pm UTC
        assert result.hour == 18

    def test_iso_8601_utc(self) -> None:
        result = parse_datetime("2026-03-15T14:00:00Z", "America/New_York")
        assert result is not None
        assert result.hour == 14  # Already UTC


class TestIsFuture:
    def test_future_date(self) -> None:
        future = datetime(2099, 1, 1, tzinfo=UTC)
        assert is_future(future) is True

    def test_past_date(self) -> None:
        past = datetime(2020, 1, 1, tzinfo=UTC)
        assert is_future(past) is False
