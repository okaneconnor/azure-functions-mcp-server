"""Thread-safe circuit breaker for Azure DevOps API calls.

States:
- CLOSED: requests flow normally; failures are counted.
- OPEN: requests are blocked immediately; transitions to HALF_OPEN after cooldown.
- HALF_OPEN: one probe request allowed; success resets, failure reopens.
"""

import enum
import threading
import time


class CircuitState(enum.Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 5,
        cooldown_seconds: float = 60.0,
        success_threshold: int = 1,
    ):
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._success_threshold = success_threshold

        self._lock = threading.Lock()
        self._state = CircuitState.CLOSED
        self._failure_count = 0
        self._success_count = 0
        self._opened_at: float = 0.0

    @property
    def state(self) -> CircuitState:
        with self._lock:
            self._maybe_transition_to_half_open()
            return self._state

    def allow_request(self) -> bool:
        """Return True if a request should proceed, False if circuit is open."""
        with self._lock:
            self._maybe_transition_to_half_open()
            if self._state == CircuitState.CLOSED:
                return True
            if self._state == CircuitState.HALF_OPEN:
                return True
            return False  # OPEN

    def record_success(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._success_count += 1
                if self._success_count >= self._success_threshold:
                    self._state = CircuitState.CLOSED
                    self._failure_count = 0
                    self._success_count = 0
            elif self._state == CircuitState.CLOSED:
                self._failure_count = 0

    def record_failure(self) -> None:
        with self._lock:
            if self._state == CircuitState.HALF_OPEN:
                self._state = CircuitState.OPEN
                self._opened_at = time.monotonic()
                self._success_count = 0
            elif self._state == CircuitState.CLOSED:
                self._failure_count += 1
                if self._failure_count >= self._failure_threshold:
                    self._state = CircuitState.OPEN
                    self._opened_at = time.monotonic()

    def _maybe_transition_to_half_open(self) -> None:
        """Must be called while holding self._lock."""
        if self._state == CircuitState.OPEN:
            if time.monotonic() - self._opened_at >= self._cooldown_seconds:
                self._state = CircuitState.HALF_OPEN
                self._success_count = 0
