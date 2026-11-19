"""The LLM rate guard and LLM metrics (app/ai/guard.py). No network, no sleeping: the clock
and sleep are fakes."""

import pytest
from prometheus_client import REGISTRY

from app.ai.guard import (
    InstrumentedLLM,
    LLMRateLimited,
    RateLimitedLLM,
    RateLimiter,
    guard_llm,
    shared_limiter,
)
from app.ai.llm import LLMError, LLMTimeout, MockLLM
from app.ai.settings import AISettings


class FakeTime:
    def __init__(self):
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.slept.append(seconds)
        self.now += seconds


def metric(name: str, **labels) -> float:
    return REGISTRY.get_sample_value(name, labels) or 0.0


def test_try_reserve_allows_max_calls_then_reports_the_wait():
    t = FakeTime()
    lim = RateLimiter(2, window=60, clock=t.clock, sleep=t.sleep)
    assert lim.try_reserve(0) == 0
    assert lim.try_reserve(0) == 0
    assert lim.try_reserve(0) is None  # full, and we are not willing to wait
    assert lim.try_reserve(60) == pytest.approx(60)  # willing: the wait is a full window
    t.now += 30
    assert lim.try_reserve(60) == pytest.approx(30)  # the next slot is also at +60, 30 s away


def test_a_refused_reservation_takes_no_slot():
    t = FakeTime()
    lim = RateLimiter(1, window=60, clock=t.clock, sleep=t.sleep)
    assert lim.try_reserve(0) == 0
    for _ in range(5):
        assert lim.try_reserve(10) is None
    t.now += 60
    assert lim.try_reserve(0) == 0  # window passed; the refusals reserved nothing


def test_never_more_than_max_calls_in_any_window():
    t = FakeTime()
    lim = RateLimiter(3, window=60, clock=t.clock, sleep=t.sleep)
    starts = []
    for _ in range(10):
        wait = lim.try_reserve(1000)
        starts.append(t.now + wait)
    for i, s in enumerate(starts):
        assert sum(1 for x in starts if s <= x < s + 60) <= 3, (i, starts)


def test_guard_waits_for_a_slot_when_it_fits_in_the_deadline():
    t = FakeTime()
    lim = RateLimiter(1, window=10, clock=t.clock, sleep=t.sleep)
    llm = RateLimitedLLM(MockLLM(), lim, sleep=t.sleep)
    prompt = "<exception>" + _exc_json() + "</exception>"
    llm.complete("s", prompt, timeout=25)
    llm.complete("s", prompt, timeout=25)  # must wait 10 s, which fits
    assert t.slept == [pytest.approx(10)]


def test_guard_refuses_when_the_wait_would_pass_the_deadline():
    t = FakeTime()
    lim = RateLimiter(1, window=60, clock=t.clock, sleep=t.sleep)
    inner = MockLLM()
    llm = RateLimitedLLM(inner, lim, sleep=t.sleep)
    prompt = "<exception>" + _exc_json() + "</exception>"
    llm.complete("s", prompt, timeout=25)
    with pytest.raises(LLMRateLimited):
        llm.complete("s", prompt, timeout=25)  # needs 60 s, only 25 left
    assert inner.calls == 1  # the model was not called
    assert t.slept == []


def test_workflow_falls_back_when_the_guard_refuses():
    from app.ai.retrieval import InMemoryRetriever
    from app.ai.workflow import run_workflow
    from app.contracts import PolicyConfig, ProposalSource
    from app.domain.rules import detect_exceptions
    from app.validator import validate_proposal
    from tests.sample_timecards import missed_punch

    tc = missed_punch()
    exc = detect_exceptions([tc])[0]
    t = FakeTime()
    lim = RateLimiter(1, window=60, clock=t.clock, sleep=t.sleep)
    lim.try_reserve(0)  # the minute's only call is already used
    llm = RateLimitedLLM(MockLLM(), lim, sleep=t.sleep)
    proposal, events = run_workflow(
        exc,
        tc,
        retriever=InMemoryRetriever(),
        llm=llm,
        validator=validate_proposal,
        cfg=PolicyConfig(),
        settings=AISettings(llm_provider="mock"),
    )
    assert proposal.source is ProposalSource.RULES_FALLBACK
    assert proposal.status == "PENDING_REVIEW"
    assert "LLM_ERROR" in [e.event_type for e in events]


def test_instrumented_llm_counts_errors_by_kind_and_times_calls():
    before_timeout = metric("llm_errors_total", kind="timeout")
    before_429 = metric("llm_errors_total", kind="provider_429")
    before_count = metric("llm_request_duration_seconds_count")
    prompt = "<exception>" + _exc_json() + "</exception>"

    InstrumentedLLM(MockLLM("ok")).complete("s", prompt)
    with pytest.raises(LLMTimeout):
        InstrumentedLLM(MockLLM("timeout")).complete("s", prompt)
    with pytest.raises(LLMError):
        InstrumentedLLM(MockLLM("rate_limited")).complete("s", prompt)

    assert metric("llm_errors_total", kind="timeout") == before_timeout + 1
    assert metric("llm_errors_total", kind="provider_429") == before_429 + 1
    assert metric("llm_request_duration_seconds_count") == before_count + 3


def test_guard_llm_limits_real_providers_only():
    mock = guard_llm(MockLLM(), AISettings(llm_provider="mock"))
    assert isinstance(mock, InstrumentedLLM) and isinstance(mock.inner, MockLLM)

    real = guard_llm(MockLLM(), AISettings(llm_provider="openai_compatible", llm_max_rpm=5))
    assert isinstance(real.inner, RateLimitedLLM)

    unlimited = guard_llm(MockLLM(), AISettings(llm_provider="openai_compatible", llm_max_rpm=0))
    assert isinstance(unlimited.inner, MockLLM)


def test_one_shared_limiter_per_rate():
    assert shared_limiter(7) is shared_limiter(7)
    assert shared_limiter(7) is not shared_limiter(8)


def test_max_rpm_default_and_env():
    assert AISettings.from_env({}).llm_max_rpm == 5
    assert AISettings.from_env({"LLM_MAX_RPM": "12"}).llm_max_rpm == 12


def _exc_json() -> str:
    from app.domain.rules import detect_exceptions
    from tests.sample_timecards import missed_punch

    return detect_exceptions([missed_punch()])[0].model_dump_json()
