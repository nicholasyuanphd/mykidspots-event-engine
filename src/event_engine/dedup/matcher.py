"""Jaccard similarity-based fallback deduplication.

Used for sources that don't provide stable external IDs. Compares event titles
within the same city and day using word-level Jaccard similarity.
"""

import re


def jaccard_similarity(text_a: str, text_b: str) -> float:
    """Compute word-level Jaccard similarity between two strings.

    Jaccard(A, B) = |A ∩ B| / |A ∪ B|

    Both strings are normalized to lowercase and split into words.
    Returns a float between 0.0 (no overlap) and 1.0 (identical).
    """
    words_a = _tokenize(text_a)
    words_b = _tokenize(text_b)

    if not words_a and not words_b:
        return 1.0  # Both empty = identical
    if not words_a or not words_b:
        return 0.0

    intersection = words_a & words_b
    union = words_a | words_b

    return len(intersection) / len(union)


def is_duplicate(title_a: str, title_b: str, threshold: float = 0.75) -> bool:
    """Check if two event titles are likely duplicates.

    Uses Jaccard similarity with a default threshold of 0.75, matching
    the existing MyKidSpots seed-insert dedup logic.

    Args:
        title_a: First event title.
        title_b: Second event title.
        threshold: Minimum Jaccard similarity to consider a duplicate.

    Returns:
        True if the titles are similar enough to be duplicates.
    """
    return jaccard_similarity(title_a, title_b) >= threshold


def _tokenize(text: str) -> set[str]:
    """Normalize and tokenize text into a set of words."""
    text = text.lower().strip()
    # Remove punctuation and split on whitespace
    words = re.findall(r"\w+", text)
    return set(words)
