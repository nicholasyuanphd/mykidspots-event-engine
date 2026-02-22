"""Deduplication utilities."""

from event_engine.dedup.fingerprint import compute_fingerprint
from event_engine.dedup.matcher import is_duplicate, jaccard_similarity

__all__ = ["compute_fingerprint", "is_duplicate", "jaccard_similarity"]
