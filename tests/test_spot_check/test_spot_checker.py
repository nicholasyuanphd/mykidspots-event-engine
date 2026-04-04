"""Tests for the weekly random spot-check (Phase 2)."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from event_engine.classify.ai_classifier import ClassificationResult
from event_engine.spot_check.spot_checker import run_spot_check, SpotCheckResult


def _make_pool() -> MagicMock:
    conn = AsyncMock()
    conn.execute.return_value = "UPDATE 0"
    pool = MagicMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _make_classifier(verdicts: list[ClassificationResult]) -> AsyncMock:
    classifier = AsyncMock()
    classifier.classify_batch.return_value = verdicts
    return classifier


@pytest.mark.asyncio
async def test_spot_check_passes_clean_source():
    """A source with all-passing events is not downgraded."""
    pool = _make_pool()
    classifier = _make_classifier([ClassificationResult.YES] * 5)

    with patch("event_engine.spot_check.spot_checker.fetch_verified_cvb_sources",
               new_callable=AsyncMock, return_value=["visitraleigh-events"]), \
         patch("event_engine.spot_check.spot_checker.fetch_random_active_events",
               new_callable=AsyncMock, return_value=[
                   {"id": str(i), "title": f"Event {i}", "source_url": "https://www.visitraleigh.com/e"}
                   for i in range(5)
               ]), \
         patch("event_engine.spot_check.spot_checker.upsert_trust_override",
               new_callable=AsyncMock) as mock_upsert, \
         patch("event_engine.spot_check.spot_checker._domain_for_source",
               return_value="www.visitraleigh.com"):

        result = await run_spot_check(
            pool=pool,
            classifier=classifier,
            sources_dir=None,
        )

    assert len(result.source_results) == 1
    assert result.source_results["visitraleigh-events"]["downgraded"] is False
    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_spot_check_downgrades_failing_source():
    """A source with >=40% failing events gets downgraded to trust_level='new'."""
    pool = _make_pool()
    # 3/5 fail = 60% fail rate -> exceeds 40% threshold
    classifier = _make_classifier([
        ClassificationResult.NO, ClassificationResult.NO, ClassificationResult.NO,
        ClassificationResult.YES, ClassificationResult.YES,
    ])

    with patch("event_engine.spot_check.spot_checker.fetch_verified_cvb_sources",
               new_callable=AsyncMock, return_value=["badcvb-events"]), \
         patch("event_engine.spot_check.spot_checker.fetch_random_active_events",
               new_callable=AsyncMock, return_value=[
                   {"id": str(i), "title": f"Event {i}", "source_url": "https://www.badcvb.com/e"}
                   for i in range(5)
               ]), \
         patch("event_engine.spot_check.spot_checker.upsert_trust_override",
               new_callable=AsyncMock) as mock_upsert, \
         patch("event_engine.spot_check.spot_checker._domain_for_source",
               return_value="www.badcvb.com"):

        result = await run_spot_check(
            pool=pool,
            classifier=classifier,
            sources_dir=None,
        )

    assert result.source_results["badcvb-events"]["downgraded"] is True
    mock_upsert.assert_called_once_with(pool, "badcvb-events", "new")


@pytest.mark.asyncio
async def test_spot_check_skips_source_with_no_active_events():
    """A source with no active events is skipped (not downgraded)."""
    pool = _make_pool()
    classifier = _make_classifier([])

    with patch("event_engine.spot_check.spot_checker.fetch_verified_cvb_sources",
               new_callable=AsyncMock, return_value=["empty-source"]), \
         patch("event_engine.spot_check.spot_checker.fetch_random_active_events",
               new_callable=AsyncMock, return_value=[]), \
         patch("event_engine.spot_check.spot_checker.upsert_trust_override",
               new_callable=AsyncMock) as mock_upsert, \
         patch("event_engine.spot_check.spot_checker._domain_for_source",
               return_value="www.empty.com"):

        result = await run_spot_check(
            pool=pool,
            classifier=classifier,
            sources_dir=None,
        )

    assert result.source_results["empty-source"]["skipped"] is True
    mock_upsert.assert_not_called()
