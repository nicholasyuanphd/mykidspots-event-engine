"""Per-source circuit breaker to prevent cascading failures."""

import time
from enum import Enum


class CircuitState(Enum):
    CLOSED = "closed"  # Normal operation
    OPEN = "open"  # Failing, skip requests
    HALF_OPEN = "half_open"  # Testing recovery


class CircuitBreaker:
    """Simple circuit breaker for per-source failure isolation.

    - CLOSED: Normal operation. Failures increment the counter.
    - OPEN: Source is broken. Skip all requests for cooldown_seconds.
    - HALF_OPEN: After cooldown, allow one test request.

    If the test succeeds → CLOSED. If it fails → OPEN again.
    """

    def __init__(self, failure_threshold: int = 3, cooldown_seconds: float = 300) -> None:
        self.failure_threshold = failure_threshold
        self.cooldown_seconds = cooldown_seconds
        self._failures: dict[str, int] = {}
        self._state: dict[str, CircuitState] = {}
        self._last_failure_time: dict[str, float] = {}

    def can_execute(self, source_id: str) -> bool:
        """Check if a request to this source should proceed."""
        state = self._state.get(source_id, CircuitState.CLOSED)

        if state == CircuitState.CLOSED:
            return True

        if state == CircuitState.OPEN:
            # Check if cooldown has elapsed
            last_failure = self._last_failure_time.get(source_id, 0)
            if time.monotonic() - last_failure >= self.cooldown_seconds:
                self._state[source_id] = CircuitState.HALF_OPEN
                return True
            return False

        # HALF_OPEN: allow one test request
        return True

    def record_success(self, source_id: str) -> None:
        """Record a successful request — reset failures."""
        self._failures[source_id] = 0
        self._state[source_id] = CircuitState.CLOSED

    def record_failure(self, source_id: str) -> None:
        """Record a failed request — may trip the circuit."""
        self._failures[source_id] = self._failures.get(source_id, 0) + 1
        self._last_failure_time[source_id] = time.monotonic()

        if self._failures[source_id] >= self.failure_threshold:
            self._state[source_id] = CircuitState.OPEN

    def get_state(self, source_id: str) -> CircuitState:
        """Get the current circuit state for a source."""
        return self._state.get(source_id, CircuitState.CLOSED)
