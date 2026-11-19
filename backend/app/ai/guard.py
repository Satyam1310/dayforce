"""Guards around the LLM client for the API: a rate limit and metrics.

RateLimiter is the sliding-window limiter the smoke and evaluation scripts already used; it
moved here so the API can use it too (scripts/smoke_llm.py imports it from this module).

LLM_MAX_RPM (default 5) caps real model calls. IMPORTANT: the limiter lives in this process,
so with several replicas (Kubernetes) the limit is PER REPLICA: N replicas can send up to
N * LLM_MAX_RPM calls a minute. Set LLM_MAX_RPM to (provider limit / replicas), or use one
shared limiter (for example Redis) if the replica count grows.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from collections.abc import Callable

from app.observability.metrics import LLM_ERRORS, LLM_LATENCY

from .llm import LLM, LLMError, LLMTimeout
from .settings import AISettings

DEFAULT_WAIT_BUDGET = 25.0  # used when a caller passes no timeout


class RateLimiter:
    """Sliding window: at most `max_calls` calls in any `window` seconds.

    `acquire()` blocks (sleeps) when the window is full (scripts, one thread).
    `try_reserve(max_wait)` is the thread-safe version for the API: it returns how long the
    caller must wait, or None when that wait would be longer than `max_wait` (nothing is
    reserved then). `slept` is the total time spent waiting in `acquire()`.
    Clock and sleep are injectable for tests.
    """

    def __init__(
        self,
        max_calls: int,
        window: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if max_calls < 1:
            raise ValueError("max_calls must be at least 1")
        self.max_calls = max_calls
        self.window = window
        self._clock = clock
        self._sleep = sleep
        self._stamps: deque[float] = deque()
        self._lock = threading.Lock()
        self.slept = 0.0

    def acquire(self) -> None:
        now = self._clock()
        while self._stamps and now - self._stamps[0] >= self.window:
            self._stamps.popleft()
        if len(self._stamps) >= self.max_calls:
            oldest = self._stamps.popleft()  # we wait until this call leaves the window
            wait = oldest + self.window - now
            self._sleep(wait)
            self.slept += wait
            # Never stamp earlier than the moment the oldest call expired, so no window of
            # `window` seconds can ever hold more than max_calls calls (immune to clock rounding).
            now = max(self._clock(), oldest + self.window)
        self._stamps.append(now)

    def try_reserve(self, max_wait: float) -> float | None:
        with self._lock:
            now = self._clock()
            while self._stamps and self._stamps[0] + self.window <= now:
                self._stamps.popleft()
            slot = now
            if len(self._stamps) >= self.max_calls:
                # the new call may start once the max_calls-th most recent call leaves the window
                slot = self._stamps[-self.max_calls] + self.window
            wait = max(0.0, slot - now)
            if wait > max_wait:
                return None
            self._stamps.append(slot)
            return wait


def is_rate_limited(err: BaseException) -> bool:
    resp = getattr(err.__cause__, "response", None)
    return getattr(resp, "status_code", None) == 429 or "429" in str(err)


class LLMRateLimited(LLMError):
    """Our own limit (LLM_MAX_RPM) would make this call wait past the deadline."""


class InstrumentedLLM:
    """Times every call and counts failures (by kind) for /metrics. Errors pass through
    unchanged, so the workflow still falls back exactly as before. Never records prompts,
    replies or error text."""

    def __init__(self, inner: LLM):
        self.inner = inner

    @property
    def model_name(self) -> str:
        return self.inner.model_name

    def complete(self, system: str, user: str, timeout: float | None = None) -> str:
        start = time.perf_counter()
        try:
            return self.inner.complete(system, user, timeout=timeout)
        except LLMError as e:
            LLM_ERRORS.labels(_kind(e)).inc()
            raise
        finally:
            LLM_LATENCY.observe(time.perf_counter() - start)


def _kind(e: LLMError) -> str:
    if isinstance(e, LLMRateLimited):
        return "local_rate_limit"
    if isinstance(e, LLMTimeout):
        return "timeout"
    if is_rate_limited(e):
        return "provider_429"
    return "other"


class RateLimitedLLM:
    """Waits for a free slot before a real call. If the wait would not fit in the time left
    (the workflow passes its remaining deadline as `timeout`), it raises LLMRateLimited
    instead: the workflow then falls back to the rule-based fix, as for any LLM failure."""

    def __init__(
        self, inner: LLM, limiter: RateLimiter, sleep: Callable[[float], None] = time.sleep
    ):
        self.inner = inner
        self.limiter = limiter
        self._sleep = sleep

    @property
    def model_name(self) -> str:
        return self.inner.model_name

    def complete(self, system: str, user: str, timeout: float | None = None) -> str:
        budget = DEFAULT_WAIT_BUDGET if timeout is None else timeout
        wait = self.limiter.try_reserve(budget)
        if wait is None:
            raise LLMRateLimited("rate limit: waiting would exceed the deadline")
        if wait > 0:
            self._sleep(wait)
        return self.inner.complete(system, user, timeout=max(budget - wait, 0.001))


_shared: dict[int, RateLimiter] = {}
_shared_lock = threading.Lock()


def shared_limiter(max_rpm: int) -> RateLimiter:
    """One limiter per process (and per max_rpm value), shared by all requests."""
    with _shared_lock:
        if max_rpm not in _shared:
            _shared[max_rpm] = RateLimiter(max_rpm)
        return _shared[max_rpm]


def guard_llm(llm: LLM, settings: AISettings) -> LLM:
    """Wrap an LLM for API use: metrics always; the rate limit only for real providers
    (the mock needs none). LLM_MAX_RPM=0 turns the limit off."""
    if settings.llm_provider != "mock" and settings.llm_max_rpm > 0:
        llm = RateLimitedLLM(llm, shared_limiter(settings.llm_max_rpm))
    return InstrumentedLLM(llm)
