"""Tests for event blocklist."""
import json
import pytest
from pathlib import Path
from event_engine.blocklist import is_blocked, add_to_blocklist


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
