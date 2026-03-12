"""Tests for AI event classifier."""
import pytest
from unittest.mock import AsyncMock, patch, MagicMock

from event_engine.classify.ai_classifier import AIClassifier, ClassificationResult


def test_classification_result_values():
    """ClassificationResult has expected values."""
    assert ClassificationResult.YES == "yes"
    assert ClassificationResult.NO == "no"
    assert ClassificationResult.MAYBE == "maybe"


@pytest.mark.asyncio
async def test_classify_batch_returns_one_result_per_event():
    """classify_batch returns exactly one result per input event."""
    classifier = AIClassifier(api_key="test-key")
    titles = ["Kids Storytime", "Board of Commissioners Meeting", "Family Fun Day"]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="yes\nno\nyes")]

    with patch.object(classifier._client.messages, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_response
        results = await classifier.classify_batch(titles)

    assert len(results) == len(titles)


@pytest.mark.asyncio
async def test_classify_batch_parses_yes_no_maybe():
    """classify_batch correctly parses yes/no/maybe from response."""
    classifier = AIClassifier(api_key="test-key")
    titles = ["Toddler Yoga", "Zoning Variance Meeting", "Community BBQ"]

    mock_response = MagicMock()
    mock_response.content = [MagicMock(text="yes\nno\nmaybe")]

    with patch.object(classifier._client.messages, "create", new_callable=AsyncMock) as mock_create:
        mock_create.return_value = mock_response
        results = await classifier.classify_batch(titles)

    assert results[0] == ClassificationResult.YES
    assert results[1] == ClassificationResult.NO
    assert results[2] == ClassificationResult.MAYBE


@pytest.mark.asyncio
async def test_classify_batch_falls_back_on_api_error():
    """classify_batch returns MAYBE for all events when API fails."""
    classifier = AIClassifier(api_key="test-key")
    titles = ["Some Event", "Another Event"]

    with patch.object(classifier._client.messages, "create", new_callable=AsyncMock) as mock_create:
        mock_create.side_effect = Exception("API unavailable")
        results = await classifier.classify_batch(titles)

    assert all(r == ClassificationResult.MAYBE for r in results)


@pytest.mark.asyncio
async def test_classify_batch_handles_empty_list():
    """classify_batch handles empty input without API call."""
    classifier = AIClassifier(api_key="test-key")

    with patch.object(classifier._client.messages, "create", new_callable=AsyncMock) as mock_create:
        results = await classifier.classify_batch([])
        mock_create.assert_not_called()

    assert results == []
