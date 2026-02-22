"""Tests for cost parsing."""

from decimal import Decimal

import pytest

from event_engine.normalize.cost_parser import parse_cost


class TestFreeParsing:
    @pytest.mark.parametrize(
        "text",
        ["Free", "FREE", "free", "No cost", "No charge", "$0", "$0.00", "Complimentary"],
    )
    def test_free_keywords(self, text: str) -> None:
        cost_type, amount = parse_cost(text)
        assert cost_type == "free"
        assert amount is None


class TestPaidParsing:
    def test_dollar_amount(self) -> None:
        cost_type, amount = parse_cost("$10")
        assert cost_type == "paid"
        assert amount == Decimal("10")

    def test_dollar_with_cents(self) -> None:
        cost_type, amount = parse_cost("$15.50")
        assert cost_type == "paid"
        assert amount == Decimal("15.50")

    def test_dollar_with_text(self) -> None:
        cost_type, amount = parse_cost("$5 per child")
        assert cost_type == "paid"
        assert amount == Decimal("5")

    def test_dollar_with_comma(self) -> None:
        cost_type, amount = parse_cost("$1,000")
        assert cost_type == "paid"
        assert amount == Decimal("1000")


class TestVariesParsing:
    @pytest.mark.parametrize(
        "text",
        ["Varies", "varies", "Variable pricing", "TBD"],
    )
    def test_varies_keywords(self, text: str) -> None:
        cost_type, amount = parse_cost(text)
        assert cost_type == "varies"
        assert amount is None


class TestDefaults:
    def test_empty_string(self) -> None:
        cost_type, amount = parse_cost("")
        assert cost_type == "free"
        assert amount is None

    def test_none_like(self) -> None:
        cost_type, amount = parse_cost("  ")
        assert cost_type == "free"
        assert amount is None
