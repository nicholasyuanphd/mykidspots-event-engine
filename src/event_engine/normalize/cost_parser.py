"""Cost parsing — extract cost type and amount from text."""

import re
from decimal import Decimal, InvalidOperation

from event_engine.models.normalized_event import CostType


def parse_cost(raw_cost_text: str) -> tuple[CostType, Decimal | None]:
    """Parse cost information from text.

    Args:
        raw_cost_text: Raw cost text (e.g., 'Free', '$10', '$5 per child', 'Varies').

    Returns:
        Tuple of (cost_type, cost_amount).
        cost_amount is None for free/varies events.
    """
    if not raw_cost_text:
        return ("free", None)

    text = raw_cost_text.strip().lower()

    # Free
    if text in ("free", "no cost", "no charge", "$0", "$0.00", "complimentary"):
        return ("free", None)

    # Varies
    if "varies" in text or "variable" in text or "tbd" in text:
        return ("varies", None)

    # Try to extract a dollar amount
    match = re.search(r"\$\s*([\d,]+(?:\.\d{2})?)", raw_cost_text)
    if match:
        try:
            amount = Decimal(match.group(1).replace(",", ""))
            if amount == 0:
                return ("free", None)
            return ("paid", amount)
        except InvalidOperation:
            pass

    # If it contains a number, assume paid
    num_match = re.search(r"(\d+(?:\.\d{2})?)", text)
    if num_match:
        try:
            amount = Decimal(num_match.group(1))
            if amount > 0:
                return ("paid", amount)
        except InvalidOperation:
            pass

    # Default to free (most library events are free)
    return ("free", None)
