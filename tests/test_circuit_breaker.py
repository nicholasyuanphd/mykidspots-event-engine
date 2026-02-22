"""Tests for the circuit breaker."""

from event_engine.circuit_breaker import CircuitBreaker, CircuitState


class TestCircuitBreaker:
    def test_starts_closed(self) -> None:
        cb = CircuitBreaker()
        assert cb.get_state("source-a") == CircuitState.CLOSED
        assert cb.can_execute("source-a") is True

    def test_opens_after_threshold_failures(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure("source-a")
        cb.record_failure("source-a")
        assert cb.can_execute("source-a") is True  # Still closed (2 < 3)

        cb.record_failure("source-a")  # 3rd failure
        assert cb.get_state("source-a") == CircuitState.OPEN
        assert cb.can_execute("source-a") is False

    def test_success_resets_failures(self) -> None:
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure("source-a")
        cb.record_failure("source-a")
        cb.record_success("source-a")  # Reset
        cb.record_failure("source-a")  # Only 1 failure now
        assert cb.get_state("source-a") == CircuitState.CLOSED

    def test_independent_sources(self) -> None:
        cb = CircuitBreaker(failure_threshold=2)
        cb.record_failure("source-a")
        cb.record_failure("source-a")
        assert cb.can_execute("source-a") is False
        assert cb.can_execute("source-b") is True  # Different source

    def test_half_open_after_cooldown(self) -> None:
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0)  # Instant cooldown
        cb.record_failure("source-a")
        assert cb.get_state("source-a") == CircuitState.OPEN

        # With 0 cooldown, should transition to half-open
        assert cb.can_execute("source-a") is True
        assert cb.get_state("source-a") == CircuitState.HALF_OPEN

    def test_half_open_success_closes(self) -> None:
        cb = CircuitBreaker(failure_threshold=1, cooldown_seconds=0)
        cb.record_failure("source-a")
        cb.can_execute("source-a")  # Transitions to half-open
        cb.record_success("source-a")
        assert cb.get_state("source-a") == CircuitState.CLOSED
