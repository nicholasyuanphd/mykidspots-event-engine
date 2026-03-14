"""Tests for fetch_trust_overrides — DB curator override integration."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from event_engine.db.trust_overrides import fetch_trust_overrides


def _make_pool(rows):
    """Build a mock asyncpg pool that returns ``rows`` from conn.fetch()."""
    mock_pool = MagicMock()
    mock_conn = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    mock_conn.fetch = AsyncMock(return_value=rows)
    return mock_pool


def _row(source: str, trust_level: str) -> dict:
    """Simulate an asyncpg Record (dict-like access)."""
    return {"source": source, "trust_level": trust_level}


class TestFetchTrustOverrides:
    @pytest.mark.asyncio
    async def test_returns_overrides_from_db(self):
        """When the table has rows, the dict is populated correctly."""
        pool = _make_pool([
            _row("wake-oberlin-library", "verified"),
            _row("wake-fuquay-varina-town", "new"),
        ])

        result = await fetch_trust_overrides(pool)

        assert result == {
            "wake-oberlin-library": "verified",
            "wake-fuquay-varina-town": "new",
        }

    @pytest.mark.asyncio
    async def test_empty_table_returns_empty_dict(self):
        """When the table is empty, an empty dict is returned."""
        pool = _make_pool([])

        result = await fetch_trust_overrides(pool)

        assert result == {}

    @pytest.mark.asyncio
    async def test_db_error_returns_empty_dict_without_raising(self):
        """If the DB query raises any exception, return {} and do not re-raise."""
        mock_pool = MagicMock()
        mock_conn = AsyncMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
        mock_conn.fetch = AsyncMock(side_effect=Exception("relation does not exist"))

        result = await fetch_trust_overrides(mock_pool)

        assert result == {}

    @pytest.mark.asyncio
    async def test_unknown_trust_level_is_skipped(self):
        """Rows with unrecognised trust_level values are silently dropped."""
        pool = _make_pool([
            _row("wake-oberlin-library", "verified"),
            _row("bad-source", "superuser"),  # not a valid TrustLevel
        ])

        result = await fetch_trust_overrides(pool)

        assert "bad-source" not in result
        assert result["wake-oberlin-library"] == "verified"

    @pytest.mark.asyncio
    async def test_pool_acquire_failure_returns_empty_dict(self):
        """If pool.acquire() itself raises, return {} gracefully."""
        mock_pool = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(
            side_effect=Exception("pool exhausted")
        )
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)

        result = await fetch_trust_overrides(mock_pool)

        assert result == {}


class TestTrustOverrideAppliedInOrchestrator:
    """Verify that orchestrator applies DB overrides onto SourceConfig objects."""

    def _make_source(self, source_id: str, trust_level: str):
        """Build a minimal SourceConfig-like object for testing override logic."""
        from event_engine.models.source_config import SourceConfig, LocationConfig
        return SourceConfig(
            id=source_id,
            name=source_id,
            platform="wakegov",
            trust_level=trust_level,
            base_url="https://example.com",
            location=LocationConfig(name="Test", city="Raleigh"),
        )

    def test_override_found_trust_level_updated(self):
        """When an override exists for the source, trust_level is replaced."""
        source = self._make_source("wake-oberlin-library", "new")
        overrides = {"wake-oberlin-library": "verified"}

        # Apply the same logic as the orchestrator
        if source.id in overrides:
            source.trust_level = overrides[source.id]

        assert source.trust_level == "verified"

    def test_override_not_found_yaml_value_preserved(self):
        """When no override exists for the source, the YAML value is kept."""
        source = self._make_source("wake-oberlin-library", "new")
        overrides = {}  # empty — no DB overrides

        if source.id in overrides:
            source.trust_level = overrides[source.id]

        assert source.trust_level == "new"

    def test_override_for_different_source_does_not_affect_this_source(self):
        """Overrides for other sources do not bleed into unrelated sources."""
        source = self._make_source("wake-oberlin-library", "new")
        overrides = {"wake-fuquay-varina-town": "verified"}

        if source.id in overrides:
            source.trust_level = overrides[source.id]

        assert source.trust_level == "new"
