"""Tests for smoke_llm's throttle, HTTP 429 handling and multi-model report.

Mock LLM only. Where the real HTTP client is exercised, httpx.post is replaced by a stub, so
no network request is ever made and no real key is used.
"""

import dataclasses
import time

import httpx
import pytest

from app.ai.llm import LLMError, MockLLM, OpenAICompatibleLLM
from app.ai.retrieval import InMemoryRetriever
from app.ai.settings import AISettings
from app.ai.workflow import always_pass_validator, run_workflow
from app.contracts import PolicyConfig
from scripts import smoke_llm
from scripts.smoke_llm import CallStats, GuardedLLM, RateLimiter

S = AISettings()
SECRET = "sk-test-secret-key-123"


class FakeTime:
    """Fake clock whose sleep() moves the clock forward without really waiting."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def limiter(max_calls, ft, window=60.0):
    return RateLimiter(max_calls, window, clock=ft.clock, sleep=ft.sleep)


# ---------------------------------------------------------------- throttle


def test_default_rate_is_under_20_per_minute():
    assert smoke_llm.DEFAULT_MAX_RPM < 20


def test_limiter_lets_max_calls_through_then_waits():
    ft = FakeTime()
    lim = limiter(3, ft)
    for _ in range(3):
        lim.acquire()
    assert ft.sleeps == [] and lim.slept == 0
    lim.acquire()  # 4th call inside the same minute must wait for the first to age out
    assert ft.sleeps == [60.0] and lim.slept == 60.0


def test_limiter_waits_only_as_long_as_needed():
    ft = FakeTime()
    lim = limiter(3, ft)
    for t in (0, 10, 20):
        ft.now = t
        lim.acquire()
    ft.now = 25
    lim.acquire()  # oldest call was at t=0, so wait until t=60
    assert ft.sleeps == [35.0]


def test_limiter_never_exceeds_the_rate_in_any_window():
    ft = FakeTime()
    lim = limiter(5, ft)
    stamps = []
    for _ in range(60):
        lim.acquire()
        stamps.append(ft.now)
        ft.now += 0.1  # calls arrive faster than allowed
    for i in range(len(stamps) - 5):
        assert stamps[i + 5] - stamps[i] >= 60.0 - 1e-9  # never 6 calls in one minute
    assert lim.slept > 0


def test_limiter_rejects_bad_limit():
    with pytest.raises(ValueError):
        RateLimiter(0)


def test_guarded_llm_throttles_and_counts():
    ft = FakeTime()
    guarded = GuardedLLM(MockLLM(), limiter(2, ft))
    exc, tc = smoke_llm.fixture_case()
    prompt = "<exception>" + exc.model_dump_json() + "</exception>"
    for _ in range(3):
        guarded.complete("s", prompt)
    assert guarded.stats.requests == 3 and guarded.stats.errors == 0
    assert ft.sleeps == [60.0]  # the third request had to wait
    assert guarded.model_name == "mock-llm"


def test_guarded_llm_without_limiter_does_not_wait():
    exc, _ = smoke_llm.fixture_case()
    g = GuardedLLM(MockLLM(), None)
    g.complete("s", "<exception>" + exc.model_dump_json() + "</exception>")
    assert g.stats.requests == 1


def test_throttle_wait_is_not_counted_against_latency_or_the_25s_deadline():
    # The limiter really sleeps (0.3 s per extra call). The deadline is 0.1 s, so without the
    # adjustment the workflow would hit DEADLINE_EXCEEDED before its third call.
    lim = RateLimiter(1, window=0.3)
    settings = dataclasses.replace(S, max_llm_calls=3, total_deadline_seconds=0.1)
    llm = GuardedLLM(MockLLM("invalid_json"), lim)
    case = smoke_llm.build_cases(1)[0]
    res = smoke_llm.run_case_once(
        case,
        retriever=InMemoryRetriever(),
        llm=llm,
        validator=always_pass_validator,
        cfg=PolicyConfig(),
        settings=settings,
        limiter=lim,
    )
    assert "DEADLINE_EXCEEDED" not in res.event_types
    assert llm.stats.requests == 3  # all three attempts were allowed to run
    assert lim.slept >= 0.5  # it did wait about 0.6 s in total
    assert res.latency_s < 0.3  # ...but that wait is not part of the measured latency


# ---------------------------------------------------------------- HTTP 429


def stub_post(monkeypatch, status, calls=None):
    def post(url, json, headers, timeout):
        if calls is not None:
            calls.append({"url": url, "timeout": timeout})
        resp = httpx.Response(status, request=httpx.Request("POST", url), json={"error": "x"})
        return resp

    monkeypatch.setattr("httpx.post", post)


def real_client():
    return OpenAICompatibleLLM("https://api.example.test/v1", "some-model", SECRET, 20)


def test_429_falls_back_to_the_rule_based_fix(monkeypatch):
    calls = []
    stub_post(monkeypatch, 429, calls)
    guarded = GuardedLLM(real_client())
    exc, tc = smoke_llm.fixture_case()
    proposal, events = run_workflow(
        exc,
        tc,
        retriever=InMemoryRetriever(),
        llm=guarded,
        validator=always_pass_validator,
        cfg=PolicyConfig(),
        settings=S,
    )
    kinds = [e.event_type for e in events]
    assert proposal.source.value == "RULES_FALLBACK" and proposal.status == "PENDING_REVIEW"
    assert proposal.fix == exc.baseline_fix
    assert "LLM_ERROR" in kinds and "FALLBACK_USED" in kinds
    assert len(calls) == 1  # an LLM error falls back at once: no retry into a rate limit
    assert guarded.stats == CallStats(requests=1, errors=1, rate_limited=1)


def test_other_http_errors_are_errors_but_not_rate_limited(monkeypatch):
    stub_post(monkeypatch, 500)
    guarded = GuardedLLM(real_client())
    with pytest.raises(LLMError):
        guarded.complete("s", "u")
    assert guarded.stats == CallStats(requests=1, errors=1, rate_limited=0)


def test_429_is_recognised_by_message_when_there_is_no_response_object():
    assert smoke_llm._is_rate_limited(LLMError("HTTPStatusError: 429 Too Many Requests"))
    assert not smoke_llm._is_rate_limited(LLMError("ReadTimeout: slow"))


def test_429_shows_up_in_the_run_summary_and_report(monkeypatch):
    stub_post(monkeypatch, 429)
    settings = dataclasses.replace(
        S,
        llm_provider="openai_compatible",
        llm_base_url="https://api.example.test/v1",
        llm_api_key=SECRET,
    )
    mr = smoke_llm.run_model(
        "some-model",
        settings=settings,
        retriever=InMemoryRetriever(),
        cases=smoke_llm.build_cases(2),
    )
    assert mr.summary["runs"] == 6 and mr.summary["fallback_rate"] == 1.0
    assert mr.stats.requests == 6 and mr.stats.rate_limited == 6
    text = smoke_llm.render_report([mr], provider="openai_compatible", retriever="memory")
    assert "| of which HTTP 429 (rate limited) | 6 |" in text
    assert "| Fallback rate (runs using the rule-based fix) | 100.0% |" in text


# ---------------------------------------------------------------- multi-model report


def two_model_runs():
    cases = smoke_llm.build_cases(2)
    good = smoke_llm.run_model("model-good", settings=S, retriever=InMemoryRetriever(), cases=cases)
    bad_settings = dataclasses.replace(S, llm_mock_mode="invalid_json")
    bad = smoke_llm.run_model(
        "model-bad", settings=bad_settings, retriever=InMemoryRetriever(), cases=cases
    )
    return [good, bad]


def test_report_is_side_by_side_with_one_column_per_model():
    text = smoke_llm.render_report(two_model_runs(), provider="mock", retriever="memory")
    lines = text.splitlines()
    header = next(line for line in lines if line.startswith("| Metric"))
    assert [c.strip() for c in header.strip("|").split("|")] == [
        "Metric",
        "`model-good`",
        "`model-bad`",
    ]
    sep = lines[lines.index(header) + 1]
    assert sep == "|---|---|---|"

    def row(label):
        line = next(x for x in lines if x.startswith(f"| {label}"))
        return [c.strip() for c in line.strip("|").split("|")][1:]

    assert row("JSON-valid rate") == ["100.0%", "0.0%"]
    assert row("Fallback rate") == ["0.0%", "100.0%"]
    assert row("Escalation rate") == ["0.0%", "0.0%"]
    assert row("Proposals run") == ["6", "6"]
    assert row("Prompt injection")[0].endswith("followed: no")
    for model in ("model-good", "model-bad"):
        assert f"### {model}" in text  # per-case section for each model
    assert text.count("**model-good**") == 1 and text.count("**model-bad**") == 1


def test_report_has_all_requested_metrics_rows():
    text = smoke_llm.render_report(two_model_runs(), provider="mock", retriever="memory")
    for label in (
        "JSON-valid rate",
        "Citation-valid rate",
        "Fallback rate",
        "Escalation rate",
        "Latency p50",
        "Latency p95",
        "Prompt injection",
    ):
        assert f"| {label}" in text


def test_report_marks_throttle_only_for_real_providers_and_escapes_pipes():
    runs = two_model_runs()
    runs[0].model = "weird|name"
    text = smoke_llm.render_report(
        runs, provider="openai_compatible", retriever="memory", max_rpm=18
    )
    assert "weird/name" in text and "weird|name" not in text
    assert "throttled to at most 18 per minute" in text


# ---------------------------------------------------------------- model selection


def test_resolve_models():
    mock = dataclasses.replace(S, llm_provider="mock")
    real = dataclasses.replace(S, llm_provider="openai_compatible")
    assert smoke_llm.resolve_models(["a", "b", "a"], real) == ["a", "b"]
    assert smoke_llm.resolve_models(None, dataclasses.replace(real, llm_model="env-model")) == [
        "env-model"
    ]
    assert smoke_llm.resolve_models(None, mock) == ["mock-llm"]
    placeholder = dataclasses.replace(real, llm_model="<model name from the provider's model list>")
    assert smoke_llm.resolve_models(None, placeholder) == []


# ---------------------------------------------------------------- main(), end to end


def env(monkeypatch, **values):
    monkeypatch.setattr(smoke_llm, "load_env", lambda: None)
    for k in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY", "RETRIEVER"):
        monkeypatch.delenv(k, raising=False)
    for k, v in values.items():
        monkeypatch.setenv(k, v)


def test_main_mock_with_two_models(tmp_path, monkeypatch, capsys):
    env(monkeypatch)
    out = tmp_path / "r.md"
    slept = []
    monkeypatch.setattr(time, "sleep", slept.append)
    assert (
        smoke_llm.main(
            ["--mock", "--model", "m1", "--model", "m2", "--runs", "2", "--out", str(out)]
        )
        == 0
    )
    text = out.read_text()
    assert "| Metric | `m1` | `m2` |" in text and "MOCK LLM" in text
    assert slept == []  # the mock sends no requests, so there is nothing to throttle
    assert "running model: m1" in capsys.readouterr().out


def test_main_real_provider_path_with_429_and_no_key_leak(tmp_path, monkeypatch, capsys):
    env(
        monkeypatch,
        LLM_PROVIDER="openai_compatible",
        LLM_BASE_URL="https://api.example.test/v1",
        LLM_API_KEY=SECRET,
    )
    seen = []

    def post(url, json, headers, timeout):
        seen.append((json["model"], headers["Authorization"]))
        return httpx.Response(429, request=httpx.Request("POST", url), text=f"slow down {SECRET}")

    monkeypatch.setattr("httpx.post", post)
    out = tmp_path / "r.md"
    code = smoke_llm.main(
        ["--model", "m1", "--model", "m2", "--runs", "2", "--max-rpm", "1000", "--out", str(out)]
    )
    assert code == 0
    assert {m for m, _ in seen} == {"m1", "m2"} and len(seen) == 12  # 6 proposals per model
    assert all(a == f"Bearer {SECRET}" for _, a in seen)  # the key is sent, only in the header
    captured = capsys.readouterr()
    report = out.read_text()
    for text in (captured.out, captured.err, report):
        assert SECRET not in text
    assert "| of which HTTP 429 (rate limited) | 6 | 6 |" in report
    assert "| Fallback rate (runs using the rule-based fix) | 100.0% | 100.0% |" in report
    assert "throttled to at most 1000 per minute" in report


def test_main_throttles_real_requests(tmp_path, monkeypatch):
    env(
        monkeypatch,
        LLM_PROVIDER="openai_compatible",
        LLM_BASE_URL="https://api.example.test/v1",
        LLM_API_KEY=SECRET,
    )
    stub_post(monkeypatch, 429)
    ft = FakeTime()
    monkeypatch.setattr(
        smoke_llm,
        "RateLimiter",
        lambda max_calls: RateLimiter(max_calls, 60.0, clock=ft.clock, sleep=ft.sleep),
    )
    smoke_llm.main(
        ["--model", "m1", "--runs", "2", "--max-rpm", "4", "--out", str(tmp_path / "r.md")]
    )
    # 6 requests at 4 per minute: the 5th request has to wait for the first to age out
    assert ft.sleeps and sum(ft.sleeps) >= 60.0


def test_main_refuses_to_start_without_model_or_key(tmp_path, monkeypatch):
    out = str(tmp_path / "r.md")
    env(monkeypatch, LLM_PROVIDER="openai_compatible", LLM_API_KEY=SECRET)
    with pytest.raises(SystemExit) as e:
        smoke_llm.main(["--out", out])  # no --model and no LLM_MODEL
    assert e.value.code == 2
    env(
        monkeypatch,
        LLM_PROVIDER="openai_compatible",
        LLM_API_KEY="<your key, only in backend/.env>",
    )
    with pytest.raises(SystemExit) as e:
        smoke_llm.main(["--model", "m1", "--out", out])  # key is still the placeholder
    assert e.value.code == 2
    env(monkeypatch, LLM_PROVIDER="openai_compatible", LLM_API_KEY=SECRET, LLM_BASE_URL="<url>")
    with pytest.raises(SystemExit) as e:
        smoke_llm.main(["--model", "m1", "--out", out])
    assert e.value.code == 2
    assert not (tmp_path / "r.md").exists()
