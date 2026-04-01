"""Tests that tourism sources use the tourism system prompt in the orchestrator."""
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from event_engine.classify.ai_classifier import TOURISM_SYSTEM_PROMPT
from event_engine.models import SourceConfig
from event_engine.models.source_config import LocationConfig


def _make_source(platform: str) -> SourceConfig:
    return SourceConfig(
        id=f"test-{platform}",
        name=f"Test {platform}",
        platform=platform,
        trust_level="new",
        content_policy="commercial",
        enabled=True,
        timezone="America/New_York",
        base_url="https://example.com",
        location_id="test",
        location=LocationConfig(name="Test City", city="Raleigh"),
        ai_classification="required",
        request_delay_ms=0,
    )


def _make_raw_event(title: str = "Spring Festival") -> MagicMock:
    event = MagicMock()
    event.title = title
    event.source_url = ""
    event.is_recurring = False
    return event


async def _run_scrape_source(source: SourceConfig, mock_classifier: MagicMock) -> None:
    """Helper: run _scrape_source with mocked scraper and normalize."""
    from event_engine.circuit_breaker import CircuitBreaker
    from event_engine.orchestrator import _scrape_source

    mock_client = MagicMock(spec=httpx.AsyncClient)
    semaphore = asyncio.Semaphore(1)
    cb = CircuitBreaker()

    mock_scraper = MagicMock()
    mock_scraper.scrape_all = AsyncMock(return_value=[_make_raw_event()])
    mock_scraper_cls = MagicMock(return_value=mock_scraper)

    mock_normalized_event = MagicMock()

    with (
        patch("event_engine.orchestrator.get_scraper", return_value=mock_scraper_cls),
        patch("event_engine.orchestrator.normalize", return_value=mock_normalized_event),
    ):
        await _scrape_source(
            source, mock_client, None, semaphore, cb,
            dry_run=True, classifier=mock_classifier,
        )


async def test_simpleview_rest_source_uses_tourism_prompt():
    """simpleview_rest platform passes TOURISM_SYSTEM_PROMPT to classifier."""
    mock_classifier = MagicMock()
    mock_classifier.classify_all = AsyncMock(return_value=[MagicMock(value="yes")])

    source = _make_source("simpleview_rest")
    await _run_scrape_source(source, mock_classifier)

    mock_classifier.classify_all.assert_called_once()
    _, kwargs = mock_classifier.classify_all.call_args
    assert kwargs.get("system_prompt") == TOURISM_SYSTEM_PROMPT


async def test_meilisearch_source_uses_tourism_prompt():
    """meilisearch platform passes TOURISM_SYSTEM_PROMPT to classifier."""
    mock_classifier = MagicMock()
    mock_classifier.classify_all = AsyncMock(return_value=[MagicMock(value="yes")])

    source = _make_source("meilisearch")
    await _run_scrape_source(source, mock_classifier)

    mock_classifier.classify_all.assert_called_once()
    _, kwargs = mock_classifier.classify_all.call_args
    assert kwargs.get("system_prompt") == TOURISM_SYSTEM_PROMPT


async def test_non_tourism_source_uses_default_prompt():
    """Non-tourism platforms pass system_prompt=None (uses SYSTEM_PROMPT default)."""
    mock_classifier = MagicMock()
    mock_classifier.classify_all = AsyncMock(return_value=[MagicMock(value="yes")])

    source = _make_source("libcal")
    await _run_scrape_source(source, mock_classifier)

    mock_classifier.classify_all.assert_called_once()
    _, kwargs = mock_classifier.classify_all.call_args
    assert kwargs.get("system_prompt") is None
