"""Tests for category mapping."""

from event_engine.normalize.category_mapper import map_categories


class TestTagMapping:
    def test_library_tag(self) -> None:
        result = map_categories(["Libraries"])
        assert "library" in result

    def test_kids_families_tag(self) -> None:
        result = map_categories(["Kids & Families"])
        assert "family" in result

    def test_stem_tag(self) -> None:
        result = map_categories(["STEM/STEAM"])
        assert "stem" in result

    def test_multiple_tags(self) -> None:
        result = map_categories(["Libraries", "Kids & Families"])
        assert "library" in result
        assert "family" in result

    def test_unknown_tag_falls_through(self) -> None:
        result = map_categories(["Unknown Category XYZ"])
        # Should default to family since no match
        assert result == ["family"]


class TestTitleInference:
    def test_storytime(self) -> None:
        result = map_categories([], title="Baby Storytime")
        assert "storytime" in result

    def test_lego(self) -> None:
        result = map_categories([], title="LEGO Building Club")
        assert "stem" in result

    def test_craft(self) -> None:
        result = map_categories([], title="Holiday Craft Party")
        assert "crafts" in result
        assert "seasonal" in result

    def test_music(self) -> None:
        result = map_categories([], title="Music and Movement")
        assert "music" in result


class TestSourceOverrides:
    def test_overrides_always_applied(self) -> None:
        result = map_categories([], source_overrides=["library"])
        assert "library" in result

    def test_overrides_combined_with_inference(self) -> None:
        result = map_categories(["STEM/STEAM"], title="LEGO Club", source_overrides=["library"])
        assert "library" in result
        assert "stem" in result

    def test_invalid_override_ignored(self) -> None:
        result = map_categories([], source_overrides=["not-a-real-category"])
        # Should fall through to default
        assert result == ["family"]


class TestDefaults:
    def test_no_input(self) -> None:
        assert map_categories([]) == ["family"]

    def test_empty_tags(self) -> None:
        assert map_categories(["", "  "]) == ["family"]


class TestDedup:
    def test_no_duplicate_categories(self) -> None:
        result = map_categories(
            ["Libraries"], title="Library storytime", source_overrides=["library"]
        )
        assert result.count("library") == 1

    def test_sorted_output(self) -> None:
        result = map_categories(["STEM/STEAM", "Libraries"])
        assert result == sorted(result)
