"""Tests for Jaccard similarity matcher."""

from event_engine.dedup.matcher import is_duplicate, jaccard_similarity


class TestJaccardSimilarity:
    def test_identical_strings(self) -> None:
        assert jaccard_similarity("Family Storytime", "Family Storytime") == 1.0

    def test_completely_different(self) -> None:
        assert jaccard_similarity("Family Storytime", "LEGO Building Club") == 0.0

    def test_partial_overlap(self) -> None:
        score = jaccard_similarity("Baby Storytime Tuesday", "Baby Storytime Wednesday")
        # Words: {baby, storytime, tuesday} vs {baby, storytime, wednesday}
        # Intersection: 2, Union: 4 → Jaccard = 0.5
        assert 0.4 < score < 0.8

    def test_empty_strings(self) -> None:
        assert jaccard_similarity("", "") == 1.0

    def test_one_empty(self) -> None:
        assert jaccard_similarity("Family Fun", "") == 0.0

    def test_case_insensitive(self) -> None:
        assert jaccard_similarity("FAMILY STORYTIME", "family storytime") == 1.0

    def test_punctuation_ignored(self) -> None:
        assert jaccard_similarity("Family Storytime!", "Family Storytime") == 1.0


class TestIsDuplicate:
    def test_exact_match(self) -> None:
        assert is_duplicate("Family Storytime", "Family Storytime") is True

    def test_similar_enough(self) -> None:
        # "Baby Storytime" vs "Baby Story Time" — should be similar enough
        assert is_duplicate("Baby Storytime at Library", "Baby Storytime Library") is True

    def test_different_events(self) -> None:
        assert is_duplicate("Baby Storytime", "Teen LEGO Club") is False

    def test_custom_threshold(self) -> None:
        # With a very low threshold, everything matches
        assert is_duplicate("A", "B", threshold=0.0) is True

    def test_high_threshold(self) -> None:
        # With threshold=1.0, only exact word-set matches count
        assert is_duplicate("Family Storytime", "Family Storytime Fun", threshold=1.0) is False
