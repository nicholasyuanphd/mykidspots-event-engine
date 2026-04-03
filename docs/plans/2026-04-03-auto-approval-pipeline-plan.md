# Auto-Approval Pipeline Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace manual curator review for CVB tourism sources with a two-phase automated system: a one-time `audit-source` CLI command that graduates a source to verified + bulk-activates its pending events, and a weekly GitHub Actions cron that random-samples active events from verified sources and auto-downgrades any source that degrades.

**Architecture:** New `event_engine/spot_check/` module with `auditor.py` (Phase 1) and `spot_checker.py` (Phase 2). Both reuse the existing `AIClassifier` and asyncpg pool. The `source_trust_overrides` DB table (already read by the pipeline) gets a write path added here. The CLI gains subcommands. A new `spot-check.yml` GitHub Actions workflow runs Phase 2 weekly.

**Tech Stack:** Python 3.12, asyncpg, pytest-asyncio, `unittest.mock.AsyncMock`, existing `AIClassifier` (Haiku), existing `load_sources`, `TOURISM_SYSTEM_PROMPT` from `event_engine.classify.ai_classifier`.

---

## Context You Need Before Starting

### How events are tied to a source in the DB

The `events` table has no `source_id` column. Events are matched to a source by the domain in `source_url`. Given a source with `base_url = "https://www.visitraleigh.com"`, its events have `source_url` values like `"https://www.visitraleigh.com/events/some-event"`. Extract the netloc with `urllib.parse.urlparse(base_url).netloc` → `"www.visitraleigh.com"`, then query: `WHERE source_url ILIKE '%www.visitraleigh.com%'`.

### Why bulk-activation of pending events is safe

`verdict == "no"` in the ingestion pipeline returns `None` — the event is never inserted. So every `status='pending'` event in the DB already passed the AI kid-relevance filter. Bulk-activating all pending events for a source after graduation will not surface any AI-rejected events.

### source_trust_overrides table

- Columns (relevant): `source TEXT PRIMARY KEY, trust_level TEXT`
- Read by: `src/event_engine/db/trust_overrides.py`
- Written by: nothing yet — this plan adds the write path
- Upsert pattern: `INSERT INTO source_trust_overrides (source, trust_level) VALUES ($1, $2) ON CONFLICT (source) DO UPDATE SET trust_level = EXCLUDED.trust_level`

### AIClassifier API

```python
from event_engine.classify.ai_classifier import AIClassifier, ClassificationResult, TOURISM_SYSTEM_PROMPT

classifier = AIClassifier(api_key="...")
results: list[ClassificationResult] = await classifier.classify_batch(
    titles=["Kids Storytime", "Board Meeting"],
    system_prompt=TOURISM_SYSTEM_PROMPT,
)
# ClassificationResult.YES | .NO | .MAYBE
```

`classify_batch` never raises — it falls back to MAYBE on API failure.

### load_sources API

```python
from event_engine.models import SourceConfig, load_sources
from pathlib import Path

all_sources: list[SourceConfig] = load_sources(Path("./sources"))
source = next((s for s in all_sources if s.id == "visitraleigh-events"), None)
# source.base_url → "https://www.visitraleigh.com"
```

### Existing test patterns

- Tests use `pytest` + `pytest-asyncio`
- Mock DB connections with `AsyncMock` — no real DB in unit tests
- See `tests/test_classify/test_ai_classifier.py` for the `AsyncMock` + `patch.object` pattern
- Run a single test file: `uv run pytest tests/test_spot_check/ -v`

---

## Task 1: Create the spot_check module skeleton

**Files:**
- Create: `src/event_engine/spot_check/__init__.py`
- Create: `tests/test_spot_check/__init__.py`

**Step 1: Create the module package**

```bash
mkdir -p src/event_engine/spot_check tests/test_spot_check
touch src/event_engine/spot_check/__init__.py tests/test_spot_check/__init__.py
```

**Step 2: Verify import works**

```bash
uv run python -c "import event_engine.spot_check; print('ok')"
```
Expected: `ok`

**Step 3: Commit**

```bash
git add src/event_engine/spot_check/__init__.py tests/test_spot_check/__init__.py
git commit -m "feat: add spot_check module skeleton"
```

---

## Task 2: DB helpers for spot_check

These are pure SQL functions. No business logic here.

**Files:**
- Create: `src/event_engine/spot_check/db.py`
- Create: `tests/test_spot_check/test_db.py`

**Step 1: Write the failing tests**

```python
# tests/test_spot_check/test_db.py
"""Tests for spot_check DB helpers."""
import pytest
from unittest.mock import AsyncMock, MagicMock

from event_engine.spot_check.db import (
    fetch_random_pending_events,
    fetch_random_active_events,
    upsert_trust_override,
    bulk_activate_pending_events,
    fetch_verified_cvb_sources,
)


@pytest.mark.asyncio
async def test_fetch_random_pending_events_queries_by_domain():
    """fetch_random_pending_events queries events by domain and status=pending."""
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"id": "abc", "title": "Kids Day", "source_url": "https://www.visitraleigh.com/events/kids-day"}
    ]
    mock_pool = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    results = await fetch_random_pending_events(mock_pool, domain="www.visitraleigh.com", limit=10)

    assert len(results) == 1
    assert results[0]["title"] == "Kids Day"
    call_args = conn.fetch.call_args
    assert "www.visitraleigh.com" in str(call_args)
    assert "pending" in str(call_args)


@pytest.mark.asyncio
async def test_upsert_trust_override_calls_correct_sql():
    """upsert_trust_override writes source + trust_level to source_trust_overrides."""
    conn = AsyncMock()
    mock_pool = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    await upsert_trust_override(mock_pool, source_id="visitraleigh-events", trust_level="verified")

    conn.execute.assert_called_once()
    sql, *args = conn.execute.call_args[0]
    assert "source_trust_overrides" in sql
    assert "ON CONFLICT" in sql
    assert "visitraleigh-events" in args
    assert "verified" in args


@pytest.mark.asyncio
async def test_bulk_activate_pending_events_returns_count():
    """bulk_activate_pending_events returns number of rows updated."""
    conn = AsyncMock()
    conn.execute.return_value = "UPDATE 15"
    mock_pool = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    count = await bulk_activate_pending_events(mock_pool, domain="www.visitraleigh.com")

    assert count == 15


@pytest.mark.asyncio
async def test_fetch_verified_cvb_sources_filters_by_trust_level():
    """fetch_verified_cvb_sources returns only sources with trust_level=verified."""
    conn = AsyncMock()
    conn.fetch.return_value = [
        {"source": "visitraleigh-events"},
        {"source": "wilmington-beaches-events"},
    ]
    mock_pool = AsyncMock()
    mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)

    result = await fetch_verified_cvb_sources(mock_pool)

    assert result == ["visitraleigh-events", "wilmington-beaches-events"]
    call_args = conn.fetch.call_args
    assert "verified" in str(call_args)
```

**Step 2: Run tests — expect ImportError (module doesn't exist yet)**

```bash
uv run pytest tests/test_spot_check/test_db.py -v 2>&1 | head -20
```
Expected: `ImportError` or `ModuleNotFoundError`

**Step 3: Implement db.py**

```python
# src/event_engine/spot_check/db.py
"""Database helpers for spot_check: graduation audit and weekly spot-check."""

from __future__ import annotations

import structlog

logger = structlog.get_logger()


async def fetch_random_pending_events(pool, domain: str, limit: int = 10) -> list[dict]:
    """Fetch random pending events whose source_url contains the given domain.

    Args:
        pool: asyncpg connection pool.
        domain: Netloc of the source base_url (e.g. "www.visitraleigh.com").
        limit: Max number of events to return.

    Returns:
        List of dicts with keys: id, title, source_url.
    """
    sql = """
        SELECT id, title, source_url
        FROM events
        WHERE source_url ILIKE $1
          AND status = 'pending'
        ORDER BY random()
        LIMIT $2
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, f"%{domain}%", limit)
    return [dict(r) for r in rows]


async def fetch_random_active_events(pool, domain: str, limit: int = 5) -> list[dict]:
    """Fetch random active events whose source_url contains the given domain.

    Args:
        pool: asyncpg connection pool.
        domain: Netloc of the source base_url (e.g. "www.visitraleigh.com").
        limit: Max number of events to return.

    Returns:
        List of dicts with keys: id, title, source_url.
    """
    sql = """
        SELECT id, title, source_url
        FROM events
        WHERE source_url ILIKE $1
          AND status = 'active'
        ORDER BY random()
        LIMIT $2
    """
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, f"%{domain}%", limit)
    return [dict(r) for r in rows]


async def upsert_trust_override(pool, source_id: str, trust_level: str) -> None:
    """Write or update a trust override for a source.

    Args:
        pool: asyncpg connection pool.
        source_id: Source ID (e.g. "visitraleigh-events").
        trust_level: "verified" or "new".
    """
    sql = """
        INSERT INTO source_trust_overrides (source, trust_level)
        VALUES ($1, $2)
        ON CONFLICT (source) DO UPDATE SET trust_level = EXCLUDED.trust_level
    """
    async with pool.acquire() as conn:
        await conn.execute(sql, source_id, trust_level)
    logger.info("trust_override_written", source=source_id, trust_level=trust_level)


async def bulk_activate_pending_events(pool, domain: str) -> int:
    """Set status='active' on all pending events for a domain.

    Safe to call after graduation — every pending event has already passed
    the AI kid-relevance filter at ingestion time.

    Args:
        pool: asyncpg connection pool.
        domain: Netloc of the source base_url (e.g. "www.visitraleigh.com").

    Returns:
        Number of events activated.
    """
    sql = """
        UPDATE events
        SET status = 'active', updated_at = NOW()
        WHERE source_url ILIKE $1
          AND status = 'pending'
    """
    async with pool.acquire() as conn:
        result = await conn.execute(sql, f"%{domain}%")
    # asyncpg returns "UPDATE N" as a string
    count = int(result.split()[-1]) if result else 0
    logger.info("bulk_activated", domain=domain, count=count)
    return count


async def fetch_verified_cvb_sources(pool) -> list[str]:
    """Return source IDs with trust_level='verified' in source_trust_overrides.

    Returns:
        List of source ID strings.
    """
    sql = "SELECT source FROM source_trust_overrides WHERE trust_level = 'verified'"
    async with pool.acquire() as conn:
        rows = await conn.fetch(sql)
    return [row["source"] for row in rows]
```

**Step 4: Run tests — expect all pass**

```bash
uv run pytest tests/test_spot_check/test_db.py -v
```
Expected: 4 tests pass

**Step 5: Commit**

```bash
git add src/event_engine/spot_check/db.py tests/test_spot_check/test_db.py
git commit -m "feat: add spot_check DB helpers (fetch, upsert, bulk-activate)"
```

---

## Task 3: Implement auditor.py (Phase 1 — graduation audit)

**Files:**
- Create: `src/event_engine/spot_check/auditor.py`
- Create: `tests/test_spot_check/test_auditor.py`

**Step 1: Write the failing tests**

```python
# tests/test_spot_check/test_auditor.py
"""Tests for the graduation audit (Phase 1)."""
import pytest
from unittest.mock import AsyncMock, patch
from dataclasses import dataclass

from event_engine.classify.ai_classifier import ClassificationResult
from event_engine.spot_check.auditor import audit_source, AuditResult


def _make_pool(pending_rows: list[dict]) -> AsyncMock:
    """Return a mock asyncpg pool that yields the given rows for fetch."""
    conn = AsyncMock()
    conn.fetch.return_value = pending_rows
    conn.execute.return_value = f"UPDATE {len(pending_rows)}"
    pool = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
    return pool


def _make_classifier(verdicts: list[ClassificationResult]) -> AsyncMock:
    classifier = AsyncMock()
    classifier.classify_batch.return_value = verdicts
    return classifier


@pytest.mark.asyncio
async def test_audit_source_graduates_when_8_of_10_pass():
    """Graduates the source when ≥8/10 events pass (yes or maybe)."""
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

    # 5/5 pass but threshold requires total >= 5 to graduate (100% >= 80%)
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
```

**Step 2: Run tests — expect ImportError**

```bash
uv run pytest tests/test_spot_check/test_auditor.py -v 2>&1 | head -15
```
Expected: `ImportError: cannot import name 'audit_source'`

**Step 3: Implement auditor.py**

```python
# src/event_engine/spot_check/auditor.py
"""Phase 1: One-time source graduation audit.

Samples up to 10 pending events from a source, classifies them with Haiku,
and graduates the source to trust_level='verified' if ≥80% pass (YES or MAYBE).
On graduation, bulk-activates all pending events for that source.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import structlog

from event_engine.classify.ai_classifier import (
    AIClassifier,
    ClassificationResult,
    TOURISM_SYSTEM_PROMPT,
)
from event_engine.spot_check.db import (
    fetch_random_pending_events,
    upsert_trust_override,
    bulk_activate_pending_events,
)

logger = structlog.get_logger()

_SAMPLE_SIZE = 10
_PASS_THRESHOLD = 0.8  # ≥80% must pass to graduate


@dataclass
class AuditResult:
    """Result of a graduation audit for one source."""
    source_id: str
    total: int
    passed: int
    graduated: bool
    events_activated: int = 0
    verdicts: list[str] = field(default_factory=list)


async def audit_source(
    pool,
    classifier: AIClassifier,
    source_id: str,
    domain: str,
) -> AuditResult:
    """Run the graduation audit for a single source.

    Args:
        pool: asyncpg connection pool.
        classifier: AIClassifier instance (Haiku).
        source_id: Source ID string (e.g. "visitraleigh-events").
        domain: Netloc extracted from source base_url (e.g. "www.visitraleigh.com").

    Returns:
        AuditResult with pass/fail counts and graduation status.
    """
    log = logger.bind(source_id=source_id, domain=domain)

    events = await fetch_random_pending_events(pool, domain=domain, limit=_SAMPLE_SIZE)

    if not events:
        log.warning("audit_no_pending_events")
        return AuditResult(source_id=source_id, total=0, passed=0, graduated=False)

    titles = [e["title"] for e in events]
    verdicts = await classifier.classify_batch(titles, system_prompt=TOURISM_SYSTEM_PROMPT)

    passed = sum(1 for v in verdicts if v != ClassificationResult.NO)
    total = len(verdicts)
    pass_rate = passed / total if total > 0 else 0.0

    log.info(
        "audit_complete",
        total=total,
        passed=passed,
        pass_rate=round(pass_rate, 2),
        verdicts=[v.value for v in verdicts],
    )

    # Log each event + verdict for visibility
    for event, verdict in zip(events, verdicts):
        log.info(
            "audit_event_result",
            title=event["title"],
            verdict=verdict.value,
            source_url=event["source_url"],
        )

    if pass_rate < _PASS_THRESHOLD:
        log.warning(
            "audit_failed_threshold",
            pass_rate=round(pass_rate, 2),
            required=_PASS_THRESHOLD,
        )
        return AuditResult(
            source_id=source_id,
            total=total,
            passed=passed,
            graduated=False,
            verdicts=[v.value for v in verdicts],
        )

    # Graduate: write verified override + bulk activate
    await upsert_trust_override(pool, source_id=source_id, trust_level="verified")
    activated = await bulk_activate_pending_events(pool, domain=domain)

    log.info("source_graduated", events_activated=activated)

    return AuditResult(
        source_id=source_id,
        total=total,
        passed=passed,
        graduated=True,
        events_activated=activated,
        verdicts=[v.value for v in verdicts],
    )
```

**Step 4: Run tests — expect all pass**

```bash
uv run pytest tests/test_spot_check/test_auditor.py -v
```
Expected: 4 tests pass

**Step 5: Commit**

```bash
git add src/event_engine/spot_check/auditor.py tests/test_spot_check/test_auditor.py
git commit -m "feat: add graduation auditor — samples pending events, graduates source on ≥80% pass"
```

---

## Task 4: Implement spot_checker.py (Phase 2 — weekly spot-check)

**Files:**
- Create: `src/event_engine/spot_check/spot_checker.py`
- Create: `tests/test_spot_check/test_spot_checker.py`

**Step 1: Write the failing tests**

```python
# tests/test_spot_check/test_spot_checker.py
"""Tests for the weekly random spot-check (Phase 2)."""
import pytest
from unittest.mock import AsyncMock, patch

from event_engine.classify.ai_classifier import ClassificationResult
from event_engine.spot_check.spot_checker import run_spot_check, SpotCheckResult


def _make_pool() -> AsyncMock:
    conn = AsyncMock()
    conn.execute.return_value = "UPDATE 0"
    pool = AsyncMock()
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
            sources_dir=None,  # mocked
        )

    assert len(result.source_results) == 1
    assert result.source_results["visitraleigh-events"]["downgraded"] is False
    mock_upsert.assert_not_called()


@pytest.mark.asyncio
async def test_spot_check_downgrades_failing_source():
    """A source with ≥40% failing events gets downgraded to trust_level='new'."""
    pool = _make_pool()
    # 3/5 fail = 60% fail rate → exceeds 40% threshold
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
```

**Step 2: Run tests — expect ImportError**

```bash
uv run pytest tests/test_spot_check/test_spot_checker.py -v 2>&1 | head -15
```
Expected: `ImportError: cannot import name 'run_spot_check'`

**Step 3: Implement spot_checker.py**

```python
# src/event_engine/spot_check/spot_checker.py
"""Phase 2: Weekly random spot-check for verified CVB sources.

Samples 5 random active events per verified source. If ≥40% fail the
kid-relevance check, the source is auto-downgraded to trust_level='new'
so future ingestion runs land events as pending again.

Downgrade is forward-only — already-active events are not retracted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import structlog

from event_engine.classify.ai_classifier import (
    AIClassifier,
    ClassificationResult,
    TOURISM_SYSTEM_PROMPT,
)
from event_engine.models import load_sources
from event_engine.spot_check.db import (
    fetch_random_active_events,
    fetch_verified_cvb_sources,
    upsert_trust_override,
)

logger = structlog.get_logger()

_SAMPLE_SIZE = 5
_FAIL_THRESHOLD = 0.4  # ≥40% failing → downgrade


@dataclass
class SpotCheckResult:
    """Aggregate result of a weekly spot-check run."""
    sources_checked: int = 0
    sources_downgraded: int = 0
    source_results: dict[str, dict] = field(default_factory=dict)


def _domain_for_source(source_id: str, sources_dir: Path | None) -> str | None:
    """Look up the domain (netloc) for a source_id from YAML configs.

    Returns None if the source_id is not found.
    """
    from urllib.parse import urlparse

    if sources_dir is None:
        return None

    sources = load_sources(sources_dir)
    source = next((s for s in sources if s.id == source_id), None)
    if source is None:
        return None
    return urlparse(source.base_url).netloc


async def run_spot_check(
    pool,
    classifier: AIClassifier,
    sources_dir: Path | None,
) -> SpotCheckResult:
    """Run the weekly spot-check across all verified CVB sources.

    Args:
        pool: asyncpg connection pool.
        classifier: AIClassifier instance (Haiku).
        sources_dir: Path to YAML sources directory (for domain lookup).

    Returns:
        SpotCheckResult with per-source verdicts and downgrade counts.
    """
    result = SpotCheckResult()
    verified_source_ids = await fetch_verified_cvb_sources(pool)

    logger.info("spot_check_started", sources=verified_source_ids)

    for source_id in verified_source_ids:
        log = logger.bind(source_id=source_id)
        result.sources_checked += 1

        domain = _domain_for_source(source_id, sources_dir)
        if not domain:
            log.warning("spot_check_domain_not_found", source_id=source_id)
            result.source_results[source_id] = {"skipped": True, "reason": "domain_not_found"}
            continue

        events = await fetch_random_active_events(pool, domain=domain, limit=_SAMPLE_SIZE)

        if not events:
            log.info("spot_check_no_active_events", domain=domain)
            result.source_results[source_id] = {"skipped": True, "reason": "no_active_events"}
            continue

        titles = [e["title"] for e in events]
        verdicts = await classifier.classify_batch(titles, system_prompt=TOURISM_SYSTEM_PROMPT)

        failed = sum(1 for v in verdicts if v == ClassificationResult.NO)
        total = len(verdicts)
        fail_rate = failed / total if total > 0 else 0.0

        # Log every result for trend visibility
        for event, verdict in zip(events, verdicts):
            log.info(
                "spot_check_event_result",
                title=event["title"],
                verdict=verdict.value,
                source_url=event["source_url"],
            )

        log.info(
            "spot_check_source_summary",
            total=total,
            failed=failed,
            fail_rate=round(fail_rate, 2),
        )

        if fail_rate >= _FAIL_THRESHOLD:
            log.warning(
                "source_downgraded",
                fail_rate=round(fail_rate, 2),
                threshold=_FAIL_THRESHOLD,
                note="Future ingestion runs will land events as pending. Existing active events unchanged.",
            )
            await upsert_trust_override(pool, source_id=source_id, trust_level="new")
            result.sources_downgraded += 1
            result.source_results[source_id] = {
                "downgraded": True,
                "fail_rate": round(fail_rate, 2),
                "total": total,
                "failed": failed,
            }
        else:
            result.source_results[source_id] = {
                "downgraded": False,
                "fail_rate": round(fail_rate, 2),
                "total": total,
                "failed": failed,
            }

    logger.info(
        "spot_check_complete",
        sources_checked=result.sources_checked,
        sources_downgraded=result.sources_downgraded,
    )
    return result
```

**Step 4: Run tests — expect all pass**

```bash
uv run pytest tests/test_spot_check/test_spot_checker.py -v
```
Expected: 3 tests pass

**Step 5: Run all spot_check tests together**

```bash
uv run pytest tests/test_spot_check/ -v
```
Expected: 7 tests pass

**Step 6: Commit**

```bash
git add src/event_engine/spot_check/spot_checker.py tests/test_spot_check/test_spot_checker.py
git commit -m "feat: add weekly spot-checker — auto-downgrades sources with ≥40% fail rate"
```

---

## Task 5: Extend CLI with subcommands

The current CLI has a single implicit command (run the pipeline). We need to add:
- `audit-source <source-id>` — Phase 1 graduation
- `spot-check` — Phase 2 weekly run (also used by GitHub Actions)
- default (no subcommand) — existing pipeline behavior unchanged

**Files:**
- Modify: `src/event_engine/cli.py`

**Step 1: Read the current cli.py in full before editing**

```bash
cat src/event_engine/cli.py
```

**Step 2: Rewrite cli.py to support subcommands**

Replace the existing `main()` with a subcommand-aware version. The key pattern: use `argparse` subparsers. The existing `--dry-run`, `--source-filter`, `--verbose` args move under a `run` subcommand (or stay as default when no subcommand is given).

```python
# src/event_engine/cli.py  (full replacement)
"""CLI entry point for the event engine."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path

import structlog

from event_engine.config import Settings


def _configure_logging(verbose: bool) -> None:
    log_level = "DEBUG" if verbose else "INFO"
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelName(log_level)
        ),
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer(),
        ],
    )


async def _cmd_run(args: argparse.Namespace, settings: Settings) -> None:
    """Run the full scrape pipeline (existing behavior)."""
    from event_engine.orchestrator import run

    await run(
        sources_dir=str(settings.sources_dir),
        database_url=settings.database_url,
        anthropic_api_key=settings.anthropic_api_key,
        sentry_dsn=settings.sentry_dsn,
        max_concurrency=settings.max_concurrency,
        dry_run=args.dry_run,
        source_filter=args.source_filter,
    )


async def _cmd_audit_source(args: argparse.Namespace, settings: Settings) -> None:
    """Run graduation audit for a single source."""
    from urllib.parse import urlparse

    import asyncpg

    from event_engine.classify.ai_classifier import AIClassifier
    from event_engine.db.connection import create_pool
    from event_engine.models import load_sources
    from event_engine.spot_check.auditor import audit_source

    log = structlog.get_logger()

    # Resolve source → domain
    sources = load_sources(settings.sources_dir)
    source = next((s for s in sources if s.id == args.source_id), None)
    if source is None:
        # Also check disabled sources (include enabled=False)
        from event_engine.models.source_config import SourceFile
        import yaml
        for yml_path in sorted(settings.sources_dir.glob("*.yml")):
            with open(yml_path) as f:
                data = yaml.safe_load(f)
            if not data or "sources" not in data:
                continue
            sf = SourceFile.model_validate(data)
            source = next((s for s in sf.sources if s.id == args.source_id), None)
            if source:
                break

    if source is None:
        log.error("source_not_found", source_id=args.source_id)
        sys.exit(1)

    domain = urlparse(source.base_url).netloc
    log.info("audit_starting", source_id=args.source_id, domain=domain)

    pool = await create_pool(settings.database_url)
    try:
        classifier = AIClassifier(api_key=settings.anthropic_api_key)
        result = await audit_source(
            pool=pool,
            classifier=classifier,
            source_id=args.source_id,
            domain=domain,
        )
    finally:
        await pool.close()

    if result.graduated:
        log.info(
            "audit_result_graduated",
            source_id=args.source_id,
            passed=result.passed,
            total=result.total,
            events_activated=result.events_activated,
        )
    else:
        log.warning(
            "audit_result_not_graduated",
            source_id=args.source_id,
            passed=result.passed,
            total=result.total,
        )
        sys.exit(2)  # Non-zero exit so GitHub Actions / scripts can detect failure


async def _cmd_spot_check(args: argparse.Namespace, settings: Settings) -> None:
    """Run the weekly random spot-check across all verified sources."""
    from event_engine.classify.ai_classifier import AIClassifier
    from event_engine.db.connection import create_pool
    from event_engine.spot_check.spot_checker import run_spot_check

    log = structlog.get_logger()

    pool = await create_pool(settings.database_url)
    try:
        classifier = AIClassifier(api_key=settings.anthropic_api_key)
        result = await run_spot_check(
            pool=pool,
            classifier=classifier,
            sources_dir=settings.sources_dir,
        )
    finally:
        await pool.close()

    log.info(
        "spot_check_summary",
        sources_checked=result.sources_checked,
        sources_downgraded=result.sources_downgraded,
    )

    if result.sources_downgraded > 0:
        sys.exit(3)  # Non-zero so GitHub Actions marks the run as attention-needed


def main() -> None:
    """Main entry point — parse args and dispatch to subcommand."""
    parser = argparse.ArgumentParser(
        prog="event-engine",
        description="MyKidSpots Event Engine",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable debug logging")

    subparsers = parser.add_subparsers(dest="command")

    # Default: run the scrape pipeline
    run_parser = subparsers.add_parser("run", help="Run the full scrape pipeline (default)")
    run_parser.add_argument("--dry-run", action="store_true")
    run_parser.add_argument("--source-filter", type=str, default=None)

    # audit-source
    audit_parser = subparsers.add_parser("audit-source", help="Run graduation audit for a source")
    audit_parser.add_argument("source_id", type=str, help="Source ID to audit (e.g. visitraleigh-events)")

    # spot-check
    subparsers.add_parser("spot-check", help="Run weekly random spot-check on verified sources")

    args = parser.parse_args()

    # Default to 'run' if no subcommand given (backwards compatibility)
    if args.command is None:
        args.command = "run"
        args.dry_run = False
        args.source_filter = None

    _configure_logging(args.verbose)
    logger = structlog.get_logger()

    try:
        settings = Settings()  # type: ignore[call-arg]
    except Exception as e:
        logger.error("config_error", error=str(e))
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)

    dispatch = {
        "run": _cmd_run,
        "audit-source": _cmd_audit_source,
        "spot-check": _cmd_spot_check,
    }

    asyncio.run(dispatch[args.command](args, settings))


if __name__ == "__main__":
    main()
```

**Step 3: Verify existing pipeline behavior is unchanged**

```bash
uv run python -m event_engine --help
```
Expected: shows `run`, `audit-source`, `spot-check` subcommands

```bash
uv run python -m event_engine run --dry-run --source-filter "visitraleigh*" 2>&1 | head -5
```
Expected: starts scraping (or shows config error if no .env) — the point is it doesn't crash on import

**Step 4: Verify new subcommand help**

```bash
uv run python -m event_engine audit-source --help
uv run python -m event_engine spot-check --help
```
Expected: both show usage without errors

**Step 5: Check that existing tests still pass**

```bash
uv run pytest tests/ -v --ignore=tests/test_spot_check/ 2>&1 | tail -10
```
Expected: all existing tests still pass

**Step 6: Commit**

```bash
git add src/event_engine/cli.py
git commit -m "feat: extend CLI with audit-source and spot-check subcommands"
```

---

## Task 6: Check if create_pool exists; add it if not

The CLI commands call `create_pool(database_url)`. Verify this function exists.

**Step 1: Check**

```bash
grep -n "def create_pool\|create_pool" src/event_engine/db/connection.py | head -10
```

**Step 2: If `create_pool` does NOT exist**, add it to `connection.py`:

```python
async def create_pool(database_url: str):
    """Create and return an asyncpg connection pool."""
    import asyncpg
    return await asyncpg.create_pool(
        database_url,
        statement_cache_size=0,  # Required for PgBouncer pooler
    )
```

If it already exists with a different signature, adapt the CLI calls in Task 5 to match.

**Step 3: Run all spot_check tests to confirm nothing broke**

```bash
uv run pytest tests/test_spot_check/ -v
```
Expected: 7 tests pass

**Step 4: Commit (only if you added create_pool)**

```bash
git add src/event_engine/db/connection.py
git commit -m "feat: add create_pool helper to db.connection"
```

---

## Task 7: GitHub Actions workflow for weekly spot-check

**Files:**
- Create: `.github/workflows/spot-check.yml`

**Step 1: Create the workflow**

Pattern mirrors `.github/workflows/scrape.yml` exactly.

```yaml
# .github/workflows/spot-check.yml
name: Weekly Spot-Check

on:
  schedule:
    - cron: "0 14 * * 0"  # Sundays 9 AM ET (14:00 UTC)
  workflow_dispatch:        # Allow manual trigger from GitHub UI

jobs:
  spot-check:
    runs-on: ubuntu-latest
    env:
      DATABASE_URL: ${{ secrets.DATABASE_URL }}
      ANTHROPIC_API_KEY: ${{ secrets.ANTHROPIC_API_KEY }}
    steps:
      - uses: actions/checkout@v4

      - name: Install uv
        uses: astral-sh/setup-uv@v4
        with:
          version: "latest"

      - name: Set up Python
        run: uv python install 3.12

      - name: Install dependencies
        run: uv sync

      - name: Run spot-check
        run: uv run python -m event_engine spot-check --verbose
```

**Step 2: Verify the workflow file is valid YAML**

```bash
python3 -c "import yaml; yaml.safe_load(open('.github/workflows/spot-check.yml'))" && echo "valid YAML"
```
Expected: `valid YAML`

**Step 3: Check that ANTHROPIC_API_KEY is in scrape.yml secrets to confirm the secret name**

```bash
grep -n "ANTHROPIC\|anthropic" .github/workflows/scrape.yml
```

If `scrape.yml` uses a different secret name (e.g., `ANTHROPIC_API_KEY`), make sure the spot-check workflow uses the same name.

**Step 4: Commit**

```bash
git add .github/workflows/spot-check.yml
git commit -m "feat: add weekly spot-check GitHub Actions workflow (Sundays 9 AM ET)"
```

---

## Task 8: Run full test suite and verify

**Step 1: Run all spot_check tests**

```bash
uv run pytest tests/test_spot_check/ -v
```
Expected: 7 tests pass (4 db + 4 auditor - 1 = wait, let me recount: test_db.py has 4, test_auditor.py has 4, test_spot_checker.py has 3 = 11 total)

Actually: `test_db.py` = 4 tests, `test_auditor.py` = 4 tests, `test_spot_checker.py` = 3 tests = **11 tests total**

**Step 2: Run existing tests to confirm no regressions**

```bash
uv run pytest tests/ -v --ignore=tests/test_spot_check/ 2>&1 | tail -15
```
Expected: all existing tests still pass (same count as before)

**Step 3: Final commit if anything was missed**

```bash
git status
```
If clean: nothing to do. If there are untracked changes, investigate before committing.

---

## Graduation Runbook (What To Do After First Scrape)

After this is built and deployed, here's how you graduate the 6 CVB sources that already have pending events:

```bash
# In mykidspots-event-engine repo, with .env set:
uv run python -m event_engine audit-source visitraleigh-events
uv run python -m event_engine audit-source wilmington-beaches-events
uv run python -m event_engine audit-source charlottesgotalot-events
uv run python -m event_engine audit-source exploreboone-events
uv run python -m event_engine audit-source outerbanks-events
uv run python -m event_engine audit-source visitgreenvillenc-events
uv run python -m event_engine audit-source fayetteville-events
uv run python -m event_engine audit-source visitjacksonvillenc-events
```

Each command logs the sampled events + verdicts. Sources that graduate print `source_graduated`. Sources that don't exit with code 2 — investigate the logged verdicts to see which events failed.
