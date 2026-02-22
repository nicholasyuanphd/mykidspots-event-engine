"""Tests for age range parsing."""

import pytest

from event_engine.normalize.age_parser import parse_age_range


class TestExplicitAgeRanges:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("Ages 3-5", (3, 5)),
            ("ages: 3-5", (3, 5)),
            ("Ages 0-2", (0, 2)),
            ("Ages 5 to 12", (5, 12)),
            ("3-5 years", (3, 5)),
            ("3 to 5 years old", (3, 5)),
            ("Ages 5 and up", (5, 17)),
            ("Ages 13+", (13, 17)),
            ("Under 5", (0, 4)),
            ("under 3", (0, 2)),
        ],
    )
    def test_regex_patterns(self, text: str, expected: tuple[int, int]) -> None:
        assert parse_age_range(age_text=text) == expected


class TestKeywordMatching:
    @pytest.mark.parametrize(
        "text, expected",
        [
            ("Baby", (0, 1)),
            ("Infant storytime", (0, 1)),
            ("Toddler playdate", (1, 3)),
            ("Preschool LEGO", (3, 5)),
            ("Pre-K craft", (3, 5)),
            ("School Age coding", (5, 12)),
            ("Tween hangout", (10, 12)),
            ("Teen game night", (13, 17)),
            ("Family fun day", (0, 17)),
            ("All ages welcome", (0, 17)),
        ],
    )
    def test_keyword_age_mapping(self, text: str, expected: tuple[int, int]) -> None:
        assert parse_age_range(age_text=text) == expected


class TestTitleInference:
    @pytest.mark.parametrize(
        "title, expected",
        [
            ("Baby Storytime", (0, 5)),  # "storytime" keyword matches first → 0-5
            ("Toddler Playdate", (0, 3)),  # "playdate" keyword matches first → 0-3
            ("Preschool Craft Time", (3, 5)),
            ("LEGO Club", (5, 12)),
            ("STEM Saturday", (5, 12)),
            ("Teen Game Night", (13, 17)),
            ("Family Movie Night", (0, 17)),
        ],
    )
    def test_title_keyword_inference(self, title: str, expected: tuple[int, int]) -> None:
        assert parse_age_range(title=title) == expected


class TestDefaults:
    def test_no_input(self) -> None:
        assert parse_age_range() == (0, 17)

    def test_empty_strings(self) -> None:
        assert parse_age_range(age_text="", title="", description="") == (0, 17)

    def test_unrecognized_text(self) -> None:
        assert parse_age_range(age_text="General Public") == (0, 17)


class TestPriority:
    def test_explicit_age_overrides_keyword(self) -> None:
        # "Ages 8-12" in age_text should win over "baby" in title
        assert parse_age_range(age_text="Ages 8-12", title="Baby STEM") == (8, 12)

    def test_age_text_keywords_override_title(self) -> None:
        # "Toddler" in age_text should be checked before title keywords
        assert parse_age_range(age_text="Toddler", title="STEM Workshop") == (1, 3)
