"""BaseScraper — abstract base class for all event source adapters."""

import asyncio
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator

import httpx
import structlog
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from event_engine.models import RawEvent, SourceConfig

logger = structlog.get_logger()

# Retry on timeouts and server errors only
RETRYABLE_EXCEPTIONS = (httpx.TimeoutException, httpx.HTTPStatusError)


def _is_server_error(exc: BaseException) -> bool:
    """Only retry on 5xx errors, not 4xx."""
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return isinstance(exc, httpx.TimeoutException)


class BaseScraper(ABC):
    """Abstract base class for event source scrapers.

    Provides shared HTTP fetching with retry logic and rate limiting.
    Subclasses implement `scrape()` to yield RawEvent objects.
    """

    platform: str = ""
    """Platform identifier (e.g., 'libcal', 'wakegov'). Set by subclasses."""

    def __init__(self, source: SourceConfig, client: httpx.AsyncClient) -> None:
        self.source = source
        self.client = client
        self.log = logger.bind(source_id=source.id, platform=self.platform)

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        retry=retry_if_exception_type(RETRYABLE_EXCEPTIONS),
        before_sleep=lambda retry_state: structlog.get_logger().warning(
            "retrying_request",
            attempt=retry_state.attempt_number,
            wait=retry_state.next_action.sleep if retry_state.next_action else 0,
        ),
    )
    async def fetch(self, url: str, **kwargs: object) -> httpx.Response:
        """Fetch a URL with retry logic and rate limiting.

        Retries up to 3 times on timeout or 5xx errors with exponential backoff.
        Respects per-source request_delay_ms between requests.
        """
        # Rate limiting: delay between requests
        delay_s = self.source.request_delay_ms / 1000.0
        if delay_s > 0:
            await asyncio.sleep(delay_s)

        response = await self.client.get(url, **kwargs)  # type: ignore[arg-type]
        response.raise_for_status()
        return response

    async def fetch_json(self, url: str, **kwargs: object) -> dict | list:
        """Fetch a URL and parse JSON response."""
        response = await self.fetch(url, **kwargs)
        return response.json()  # type: ignore[no-any-return]

    @abstractmethod
    async def scrape(self) -> AsyncIterator[RawEvent]:
        """Yield raw events from the source.

        Subclasses must implement this method to extract events from their
        specific platform and yield RawEvent objects.
        """
        ...  # pragma: no cover
        # This yield is needed to make the type checker happy about AsyncIterator
        if False:  # type: ignore[unreachable]  # noqa: SIM108
            yield RawEvent(source_id="", external_id="", title="", raw_start="")

    async def scrape_all(self) -> list[RawEvent]:
        """Collect all events from scrape() into a list."""
        events: list[RawEvent] = []
        async for event in self.scrape():
            events.append(event)
        return events
