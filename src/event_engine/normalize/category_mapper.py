"""Category mapping — map source tags and title keywords to MyKidSpots categories."""

from event_engine.models.normalized_event import VALID_CATEGORIES

# Source tag → MyKidSpots category (case-insensitive matching)
TAG_TO_CATEGORY: dict[str, str] = {
    # Library-related
    "libraries": "library",
    "library": "library",
    "storytime": "storytime",
    "story time": "storytime",
    "book club": "reading",
    "reading": "reading",
    # Arts
    "arts & entertainment": "art",
    "arts and entertainment": "art",
    "art": "art",
    "arts & crafts": "arts-crafts",
    "crafts": "crafts",
    "music": "music",
    # STEM
    "stem": "stem",
    "steam": "stem",
    "stem/steam": "stem",
    "science": "science",
    "coding": "stem",
    "robotics": "stem",
    # Sports/outdoor
    "sports": "sports",
    "outdoor": "outdoor",
    "nature": "nature",
    # Family/community
    "kids & families": "family",
    "kids and families": "family",
    "family": "family",
    "community": "social",
    # Museums
    "museum": "museum",
    "exhibit": "museum",
    # Events
    "festival": "festival",
    "seasonal": "seasonal",
    "special event": "special-event",
    "special events": "special-event",
    # Play
    "indoor play": "indoor-play",
    "playground": "playground",
    "swimming": "swimming",
    "splash pad": "splash-pad",
    # Food
    "dining": "dining",
    "farmers market": "farmers-market",
}

# Title keywords → categories for inference
TITLE_KEYWORD_CATEGORIES: dict[str, str] = {
    "storytime": "storytime",
    "story time": "storytime",
    "read": "reading",
    "book": "reading",
    "lego": "stem",
    "stem": "stem",
    "steam": "stem",
    "coding": "stem",
    "robot": "stem",
    "science": "science",
    "craft": "crafts",
    "art": "art",
    "paint": "art",
    "draw": "art",
    "music": "music",
    "sing": "music",
    "dance": "music",
    "sport": "sports",
    "soccer": "sports",
    "nature": "nature",
    "garden": "nature",
    "hike": "outdoor",
    "outdoor": "outdoor",
    "museum": "museum",
    "festival": "festival",
    "holiday": "seasonal",
    "christmas": "seasonal",
    "halloween": "seasonal",
    "easter": "seasonal",
    "summer": "seasonal",
    "playground": "playground",
    "swim": "swimming",
    "splash": "splash-pad",
}


def map_categories(
    raw_categories: list[str],
    title: str = "",
    source_overrides: list[str] | None = None,
) -> list[str]:
    """Map source tags and title keywords to MyKidSpots canonical categories.

    Args:
        raw_categories: Tags/categories from the source.
        title: Event title for keyword inference.
        source_overrides: Category overrides from the SourceConfig.

    Returns:
        List of valid MyKidSpots categories (deduped, sorted).
        Defaults to ["family"] if no matches found.
    """
    categories: set[str] = set()

    # 1. Apply source config overrides
    if source_overrides:
        for cat in source_overrides:
            if cat in VALID_CATEGORIES:
                categories.add(cat)

    # 2. Map raw source tags
    for raw_cat in raw_categories:
        mapped = TAG_TO_CATEGORY.get(raw_cat.lower().strip())
        if mapped and mapped in VALID_CATEGORIES:
            categories.add(mapped)

    # 3. Infer from title keywords
    if title:
        title_lower = title.lower()
        for keyword, cat in TITLE_KEYWORD_CATEGORIES.items():
            if keyword in title_lower and cat in VALID_CATEGORIES:
                categories.add(cat)

    # 4. Default to "family" if nothing matched
    if not categories:
        categories.add("family")

    return sorted(categories)
