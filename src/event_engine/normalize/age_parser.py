"""Age parsing — extract min/max ages from text and keywords."""

import re

# Keyword → (age_min, age_max) mapping from the MyKidSpots seeding rules
KEYWORD_AGE_MAP: dict[str, tuple[int, int]] = {
    "baby": (0, 1),
    "babies": (0, 1),
    "infant": (0, 1),
    "infants": (0, 1),
    "newborn": (0, 1),
    "toddler": (1, 3),
    "toddlers": (1, 3),
    "preschool": (3, 5),
    "pre-k": (3, 5),
    "prek": (3, 5),
    "pre-school": (3, 5),
    "school age": (5, 12),
    "school-age": (5, 12),
    "elementary": (5, 12),
    "tween": (10, 12),
    "tweens": (10, 12),
    "teen": (13, 17),
    "teens": (13, 17),
    "teenager": (13, 17),
    "family": (0, 17),
    "families": (0, 17),
    "all ages": (0, 17),
}

# Title keywords → (age_min, age_max) for inference when no explicit age text
TITLE_KEYWORD_AGE_MAP: dict[str, tuple[int, int]] = {
    "storytime": (0, 5),
    "story time": (0, 5),
    "lego": (5, 12),
    "stem": (5, 12),
    "coding": (5, 12),
    "robotics": (5, 12),
    "playdate": (0, 3),
    "play date": (0, 3),
    "baby": (0, 1),
    "toddler": (1, 3),
    "preschool": (3, 5),
    "teen": (13, 17),
}

# Regex patterns for explicit age ranges
AGE_RANGE_PATTERNS = [
    # "Ages 3-5", "ages 3 to 5", "Ages: 3-5"
    re.compile(r"ages?\s*:?\s*(\d+)\s*[-–to]+\s*(\d+)", re.IGNORECASE),
    # "3-5 years", "3 to 5 years"
    re.compile(r"(\d+)\s*[-–to]+\s*(\d+)\s*(?:years?|yrs?)", re.IGNORECASE),
    # "Ages 5 and up", "Ages 5+"
    re.compile(r"ages?\s*:?\s*(\d+)\s*(?:and up|\+|and older)", re.IGNORECASE),
    # "Under 5", "Under 3"
    re.compile(r"under\s*(\d+)", re.IGNORECASE),
]


def parse_age_range(
    age_text: str = "",
    title: str = "",
    description: str = "",
) -> tuple[int, int]:
    """Parse age range from text fields.

    Checks in order:
    1. Explicit age text (e.g., "Ages 3-5")
    2. Keyword matching in age text
    3. Title keyword inference
    4. Default: 0-17 (all ages)

    Args:
        age_text: Explicit age range text from the source.
        title: Event title for keyword inference.
        description: Event description for additional context.

    Returns:
        Tuple of (age_min, age_max).
    """
    # 1. Try explicit regex patterns on age_text
    for text in [age_text, title, description]:
        if not text:
            continue
        result = _try_regex_patterns(text)
        if result:
            return result

    # 2. Try keyword matching on age_text
    if age_text:
        result = _try_keyword_match(age_text, KEYWORD_AGE_MAP)
        if result:
            return result

    # 3. Try title keyword inference
    if title:
        result = _try_keyword_match(title, TITLE_KEYWORD_AGE_MAP)
        if result:
            return result

    # 4. Default
    return (0, 17)


def _try_regex_patterns(text: str) -> tuple[int, int] | None:
    """Try to match explicit age patterns in text."""
    for pattern in AGE_RANGE_PATTERNS:
        match = pattern.search(text)
        if match:
            groups = match.groups()
            if len(groups) == 2:
                age_min = int(groups[0])
                age_max = int(groups[1])
                return (min(age_min, age_max), max(age_min, age_max))
            elif len(groups) == 1:
                age = int(groups[0])
                # "Under X" → 0 to X-1
                if "under" in match.group(0).lower():
                    return (0, max(0, age - 1))
                # "X and up" → X to 17
                return (age, 17)
    return None


def _try_keyword_match(
    text: str,
    keyword_map: dict[str, tuple[int, int]],
) -> tuple[int, int] | None:
    """Try to match keywords in text against a mapping."""
    text_lower = text.lower()
    for keyword, ages in keyword_map.items():
        if keyword in text_lower:
            return ages
    return None
