"""Tests for SHA-256 fingerprint deduplication."""

from event_engine.dedup.fingerprint import compute_fingerprint


class TestComputeFingerprint:
    def test_deterministic(self) -> None:
        fp1 = compute_fingerprint("source-a", "event-123")
        fp2 = compute_fingerprint("source-a", "event-123")
        assert fp1 == fp2

    def test_different_sources_different_fingerprints(self) -> None:
        fp1 = compute_fingerprint("source-a", "event-123")
        fp2 = compute_fingerprint("source-b", "event-123")
        assert fp1 != fp2

    def test_different_events_different_fingerprints(self) -> None:
        fp1 = compute_fingerprint("source-a", "event-123")
        fp2 = compute_fingerprint("source-a", "event-456")
        assert fp1 != fp2

    def test_sha256_length(self) -> None:
        fp = compute_fingerprint("source-a", "event-123")
        assert len(fp) == 64  # SHA-256 hex digest

    def test_hex_characters_only(self) -> None:
        fp = compute_fingerprint("source-a", "event-123")
        assert all(c in "0123456789abcdef" for c in fp)
