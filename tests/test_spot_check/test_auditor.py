# tests/test_spot_check/test_auditor.py
"""Tests for the graduation audit (Phase 1)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from event_engine.classify.ai_classifier import ClassificationResult
from event_engine.spot_check.auditor import audit_source, AuditResult


def _make_pool(pending_rows: list[dict]) -> MagicMock:
    """Return a mock asyncpg pool that yields the given rows for fetch."""
    conn = AsyncMock()
    conn.fetch.return_value = pending_rows
    conn.execute.return_value = f"UPDATE {len(pending_rows)}"
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _make_classifier(verdicts: list[ClassificationResult]) -> AsyncMock:
    classifier = AsyncMock()
    classifier.classify_batch.return_value = verdicts
    return classifier


@pytest.mark.asyncio
async def test_audit_source_graduates_when_8_of_10_pass():
    """Graduates the source when >=8/10 events pass (yes or maybe)."""
    pool = _make_pool([{"id": str(i), "title": f"Event {i}", "source_url": "https://www.visitraleigh.com/e"} for i in range(10)])
    verdicts = [ClassificationResult.YES] * 8 + [ClassificationResult.NO] * 2
    classifier = _make_classifier(verdicts)

    with patch("event_engine.spot_check.auditor.upsert_trust_override", new_callable=AsyncMock) as mock_upsert, \
         patch("event_engine.spot_check.auditor.bulk_activate_pending_events", new_callable=AsyncMock, return_value=10) as mock_activate:

        result = await audit_source(
            pool=pool,
            classifier=classifier,
            source_id="visitraleigh-events",
            domain="www.visitraleigh.com",
        )

    assert result.graduated is True
    assert result.passed == 8
    assert result.total == 10
    assert result.events_activated == 10
    mock_upsert.assert_called_once_with(pool, "visitraleigh-events", "verified")
    mock_activate.assert_called_once_with(pool, "www.visitraleigh.com")


@pytest.mark.asyncio
async def test_audit_source_does_not_graduate_when_fewer_than_8_pass():
    """Does not graduate when <8/10 pass."""
    pool = _make_pool([{"id": str(i), "title": f"Event {i}", "source_url": "https://www.visitraleigh.com/e"} for i in range(10)])
    verdicts = [ClassificationResult.YES] * 7 + [ClassificationResult.NO] * 3
    classifier = _make_classifier(verdicts)

    with patch("event_engine.spot_check.auditor.upsert_trust_override", new_callable=AsyncMock) as mock_upsert, \
         patch("event_engine.spot_check.auditor.bulk_activate_pending_events", new_callable=AsyncMock) as mock_activate:

        result = await audit_source(
            pool=pool,
            classifier=classifier,
            source_id="visitraleigh-events",
            domain="www.visitraleigh.com",
        )

    assert result.graduated is False
    assert result.passed == 7
    assert result.events_activated == 0
    mock_upsert.assert_not_called()
    mock_activate.assert_not_called()


@pytest.mark.asyncio
async def test_audit_source_counts_maybe_as_pass():
    """MAYBE verdicts count as passing (not failing)."""
    pool = _make_pool([{"id": str(i), "title": f"Event {i}", "source_url": "https://www.visitraleigh.com/e"} for i in range(5)])
    verdicts = [ClassificationResult.MAYBE] * 5
    classifier = _make_classifier(verdicts)

    with patch("event_engine.spot_check.auditor.upsert_trust_override", new_callable=AsyncMock), \
         patch("event_engine.spot_check.auditor.bulk_activate_pending_events", new_callable=AsyncMock, return_value=5):

        result = await audit_source(
            pool=pool,
            classifier=classifier,
            source_id="visitraleigh-events",
            domain="www.visitraleigh.com",
        )

    assert result.graduated is True
    assert result.passed == 5


@pytest.mark.asyncio
async def test_audit_source_no_pending_events_does_not_graduate():
    """If no pending events exist, audit does nothing and returns graduated=False."""
    pool = _make_pool([])
    classifier = _make_classifier([])

    with patch("event_engine.spot_check.auditor.upsert_trust_override", new_callable=AsyncMock) as mock_upsert:
        result = await audit_source(
            pool=pool,
            classifier=classifier,
            source_id="visitraleigh-events",
            domain="www.visitraleigh.com",
        )

    assert result.graduated is False
    assert result.total == 0
    mock_upsert.assert_not_called()
