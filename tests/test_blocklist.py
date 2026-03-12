"""Tests for event blocklist."""
import json
import pytest
from pathlib import Path
from event_engine.blocklist import is_blocked, add_to_blocklist, is_civic_noise


class TestBlocklist:
    def test_blocked_title_exact_match(self, tmp_path):
        blocklist_path = tmp_path / "blocklist.json"
        blocklist_path.write_text(json.dumps({
            "blocked_titles": ["Adult Book Club"],
            "blocked_patterns": [],
        }))
        assert is_blocked("Adult Book Club", blocklist_path) is True

    def test_blocked_pattern_substring(self, tmp_path):
        blocklist_path = tmp_path / "blocklist.json"
        blocklist_path.write_text(json.dumps({
            "blocked_titles": [],
            "blocked_patterns": ["adult"],
        }))
        assert is_blocked("Monthly Adult Book Discussion", blocklist_path) is True

    def test_not_blocked(self, tmp_path):
        blocklist_path = tmp_path / "blocklist.json"
        blocklist_path.write_text(json.dumps({
            "blocked_titles": [],
            "blocked_patterns": [],
        }))
        assert is_blocked("Family Storytime", blocklist_path) is False

    def test_add_to_blocklist(self, tmp_path):
        blocklist_path = tmp_path / "blocklist.json"
        blocklist_path.write_text(json.dumps({
            "blocked_titles": [],
            "blocked_patterns": [],
        }))
        add_to_blocklist("Adult Book Club", blocklist_path)
        data = json.loads(blocklist_path.read_text())
        assert "Adult Book Club" in data["blocked_titles"]

    def test_add_to_blocklist_no_duplicates(self, tmp_path):
        blocklist_path = tmp_path / "blocklist.json"
        blocklist_path.write_text(json.dumps({
            "blocked_titles": ["Adult Book Club"],
            "blocked_patterns": [],
        }))
        add_to_blocklist("Adult Book Club", blocklist_path)
        data = json.loads(blocklist_path.read_text())
        assert data["blocked_titles"].count("Adult Book Club") == 1


class TestIsCivicNoise:
    def test_board_meeting_is_civic_noise(self):
        assert is_civic_noise("Board of Adjustment Meeting") is True

    def test_public_hearing_is_civic_noise(self):
        assert is_civic_noise("Public Hearing on Zoning Ordinance 2026-01") is True

    def test_storytime_is_not_civic_noise(self):
        assert is_civic_noise("Saturday Family Storytime") is False

    def test_kids_festival_is_not_civic_noise(self):
        assert is_civic_noise("Holly Springs Kids Festival") is False

    def test_town_council_is_civic_noise(self):
        assert is_civic_noise("Town Council Regular Meeting") is True

    def test_planning_board_is_civic_noise(self):
        assert is_civic_noise("Planning Board Work Session") is True
