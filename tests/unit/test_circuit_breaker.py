"""Circuit breaker state transitions."""

from __future__ import annotations

from jobmatch_mcp.llm.circuit import CircuitBreaker


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def assert_state(breaker: CircuitBreaker, expected: str) -> None:
    assert breaker.state.value == expected


def test_opens_after_threshold_and_half_opens_after_cooldown() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=2, cooldown_seconds=10, clock=clock)

    assert breaker.allow_request() is True
    breaker.record_failure()
    assert_state(breaker, "closed")
    breaker.record_failure()
    assert_state(breaker, "open")
    assert breaker.allow_request() is False

    clock.now += 10
    assert_state(breaker, "half_open")
    assert breaker.allow_request() is True


def test_failure_in_half_open_reopens_and_success_closes() -> None:
    clock = FakeClock()
    breaker = CircuitBreaker(failure_threshold=1, cooldown_seconds=5, clock=clock)
    breaker.record_failure()
    clock.now += 5
    assert_state(breaker, "half_open")

    breaker.record_failure()
    assert_state(breaker, "open")
    assert breaker.consecutive_failures == 2

    clock.now += 5
    assert_state(breaker, "half_open")
    breaker.record_success()
    assert_state(breaker, "closed")
    assert breaker.consecutive_failures == 0
