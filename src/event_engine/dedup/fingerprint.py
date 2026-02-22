"""Fingerprint-based deduplication using SHA-256."""

import hashlib


def compute_fingerprint(source_id: str, external_id: str) -> str:
    """Compute a deterministic fingerprint for deduplication.

    The fingerprint is SHA-256(source_id:external_id), ensuring that the same
    event from the same source always produces the same fingerprint — even if
    the event's title or time changes.

    Args:
        source_id: Source config ID (e.g., 'wake-oberlin-library').
        external_id: Platform-specific event ID.

    Returns:
        Hex-encoded SHA-256 hash string.
    """
    raw = f"{source_id}:{external_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
