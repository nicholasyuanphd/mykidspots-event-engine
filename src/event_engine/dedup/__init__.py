"""Deduplication utilities."""

from event_engine.dedup.fingerprint import compute_fingerprint
from event_engine.dedup.matcher import is_duplicate, jaccard_similarity
from event_engine.dedup.series_key import compute_series_key

__all__ = ["compute_fingerprint", "compute_series_key", "is_duplicate", "jaccard_similarity"]
