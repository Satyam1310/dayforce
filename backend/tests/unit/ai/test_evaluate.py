"""Tests for scripts/evaluate.py: the report, the real-model path (stubbed, no network) and
the guards that keep a real run inside the free-tier limits."""

import dataclasses

import pytest

from app.ai.llm import MockLLM
from app.ai.settings import AISettings
from app.contracts import FixAction
from app.domain.rules import detect_exceptions
from scripts import evaluate
from tests.golden.loader import load_cases

CASES = load_cases()
SECRET = "sk-evaluate-secret-key"


@pytest.fixture(scope="module")
def mock_results():
    return evaluate.run_mock_evaluation(CASES)


# ---------------------------------------------------------------- mock report


def test_report_has_every_number_the_brief_asks_for(mock_results):
    text = evaluate.render_mock_section(mock_results)
    for needle in (
        "### Detection",
        "Precision",
        "Recall",
        "MISSED_PUNCH",
        "OVERTIME_RISK",
        "SCHEDULE_CONFLICT",
        "POLICY_BREACH",
        "False positives on clean timecards",
        "Deliberately bad fixes rejected",
        "Fell back to rules",
        "Escalated",
        "injection followed",
        "### Per-case results",
        "invalid_json",
        "bad_citation",
        "low_confidence",
        "timeout",
    ):
        assert needle in text, needle
    assert "40 of 40 cases pass every check" in text
    assert "**FAIL**" not in text


def test_write_report_keeps_the_other_section(tmp_path):
    out = tmp_path / "report.md"
    evaluate.write_report(out, "mock", "## Offline\nfirst")
    first = out.read_text()
    assert "Not run yet" in first and "## Offline\nfirst" in first
    evaluate.write_report(out, "real", "## Real model run\nnumbers")
    both = out.read_text()
    assert "numbers" in both and "first" in both and "Not run yet" not in both
    evaluate.write_report(out, "mock", "## Offline\nsecond")
    final = out.read_text()
    assert "second" in final and "first" not in final and "numbers" in final  # real kept
    assert final.count("<!-- MOCK:BEGIN -->") == 1 and final.count("<!-- REAL:BEGIN -->") == 1


def test_main_offline_writes_the_report_and_returns_zero(tmp_path, capsys):
    out = tmp_path / "r.md"
    assert evaluate.main(["--out", str(out)]) == 0
    assert "40 of 40" in out.read_text()
    assert "written to" in capsys.readouterr().out


def test_main_offline_returns_one_when_a_case_fails(tmp_path, monkeypatch):
    from app.contracts import PolicyConfig

    monkeypatch.setattr(evaluate, "CFG", PolicyConfig(daily_ot_hours=7.5))
    assert evaluate.main(["--out", str(tmp_path / "r.md")]) == 1


# ---------------------------------------------------------------- bad fixes and scripted models


def test_bad_fixes_are_independent_of_the_baseline_fix():
    count = 0
    for case in CASES:
        by_id = {t.id: t for t in case.timecards}
        for exc in detect_exceptions(case.timecards):
            bad = evaluate.bad_fixes(exc, by_id[exc.timecard_id])
            assert len(bad) >= 5, exc.evidence["rule"]
            assert exc.baseline_fix is None or all(f != exc.baseline_fix for _, f in bad)
            assert all(label for label, _ in bad)
            count += len(bad)
    assert count > 300


def test_obedient_model_and_attack_resolution():
    case = next(c for c in CASES if c.id == "inj02")
    exc = detect_exceptions(case.timecards)[0]
    attack = evaluate.resolve_attack(case.attack.fix, exc)
    assert attack.action is FixAction.ADD_PUNCH and attack.changes[0].target_id == exc.timecard_id
    llm = evaluate.ObedientLLM(attack, "POL-TA-01")
    assert "20:00" not in llm.complete("s", "u") and llm.prompts == ["u"]
    recorder = evaluate.RecordingLLM(MockLLM("ok"))
    assert recorder.model_name == "mock-llm"


def test_prompt_problem_detector():
    ok = "x <untrusted_notes>plain text</untrusted_notes> y"
    two = "x <untrusted_notes>a</untrusted_notes> <untrusted_notes>b</untrusted_notes>"
    raw = "x <untrusted_notes>a <b> c</untrusted_notes>"
    ctrl = "x <untrusted_notes>a\x07b</untrusted_notes>"
    long = "x <untrusted_notes>" + "a" * 2001 + "</untrusted_notes>"
    assert evaluate._prompt_problems([ok]) == (0, 0)
    assert evaluate._prompt_problems([two]) == (1, 0)
    assert evaluate._prompt_problems([raw, ctrl, long]) == (0, 3)


# ---------------------------------------------------------------- real model path (stubbed)


def test_real_plan_numbers():
    chosen = evaluate.real_cases(CASES)
    # inj03 has two exceptions, so the 5 injection cases give 6 proposals
    assert len(chosen) == 38 and sum(c.is_injection for c in chosen) == 6
    plan = evaluate.plan_real(chosen, 5)
    assert (plan.proposals, plan.min_requests, plan.max_requests) == (38, 38, 76)
    assert plan.minutes_low == pytest.approx(7.6) and plan.minutes_high == pytest.approx(15.2)
    assert plan.tokens_per_request > 500
    assert evaluate.plan_real([], 5).tokens_per_request == 0


def test_real_run_with_a_stub_model_and_no_waiting():
    chosen = evaluate.real_cases(CASES)[:6]
    data = evaluate.run_real_evaluation(chosen, MockLLM("ok"), None, AISettings())
    assert data["summary"]["runs"] == 6
    # one proposal needs a second request: its rule-based fix is invalid, so the mock's
    # (identical) fix is rejected, retried, and the case is escalated
    assert data["stats"].requests == 7
    # of the 6, only 3 have a rule-based fix; the mock matches it in 2 (the third is escalated)
    assert (data["agreed_with_rules"], data["with_baseline"]) == (2, 3)
    text = evaluate.render_real_section(
        data, model="stub", provider="mock", when="2026-01-01 00:00 UTC"
    )
    assert "JSON-valid rate" in text and "Per-proposal results" in text and "stub" in text


def test_real_injection_runs_are_counted():
    only_injection = [c for c in evaluate.real_cases(CASES) if c.is_injection]
    data = evaluate.run_real_evaluation(only_injection, MockLLM("ok"), None, AISettings())
    assert data["injection_runs"] == 6 and data["injection_changed_fix"] == 0


class NoWait:
    def __init__(self, max_calls):
        self.max_calls, self.slept = max_calls, 0.0

    def acquire(self):
        pass


def real_env(monkeypatch, **over):
    env = {
        "LLM_PROVIDER": "openai_compatible",
        "LLM_BASE_URL": "https://api.example.test/v1",
        "LLM_MODEL": "some-model",
        "LLM_API_KEY": SECRET,
        **over,
    }
    for k in ("LLM_PROVIDER", "LLM_BASE_URL", "LLM_MODEL", "LLM_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setattr(evaluate, "load_env", lambda: None)


def test_main_real_never_prints_or_writes_the_key(tmp_path, monkeypatch, capsys):
    real_env(monkeypatch)
    monkeypatch.setattr(evaluate, "build_llm", lambda settings: MockLLM("ok", settings.llm_model))
    monkeypatch.setattr(evaluate, "RateLimiter", NoWait)
    out = tmp_path / "r.md"
    assert evaluate.main(["--real", "--limit", "3", "--out", str(out)]) == 0
    captured = capsys.readouterr()
    for text in (captured.out, captured.err, out.read_text()):
        assert SECRET not in text
    report = out.read_text()
    assert "## Real model run" in report and "some-model" in report
    assert "Offline evaluation" not in report or "Not run yet" not in report
    assert "3 proposals" in captured.out


def test_main_real_keeps_an_existing_offline_section(tmp_path, monkeypatch):
    real_env(monkeypatch)
    monkeypatch.setattr(evaluate, "build_llm", lambda s: MockLLM("ok"))
    monkeypatch.setattr(evaluate, "RateLimiter", NoWait)
    out = tmp_path / "r.md"
    evaluate.main(["--out", str(out)])
    evaluate.main(["--real", "--limit", "2", "--out", str(out)])
    text = out.read_text()
    assert "Offline evaluation" in text and "Real model run" in text and "Not run yet" not in text


def test_main_real_refuses_unsafe_or_incomplete_settings(tmp_path, monkeypatch):
    out = str(tmp_path / "r.md")
    real_env(monkeypatch)
    with pytest.raises(SystemExit) as e:
        evaluate.main(["--real", "--max-rpm", "6", "--out", out])  # above the free-tier speed
    assert e.value.code == 2
    with pytest.raises(SystemExit):
        evaluate.main(["--real", "--max-rpm", "0", "--out", out])
    real_env(monkeypatch, LLM_PROVIDER="mock")
    with pytest.raises(SystemExit):
        evaluate.main(["--real", "--out", out])  # a mock is not a real model
    real_env(monkeypatch, LLM_API_KEY="<your key, only in backend/.env, never commit>")
    with pytest.raises(SystemExit):
        evaluate.main(["--real", "--out", out])  # placeholder key
    real_env(monkeypatch, LLM_MODEL="<model name>")
    with pytest.raises(SystemExit):
        evaluate.main(["--real", "--out", out])  # no model
    assert not (tmp_path / "r.md").exists()  # nothing was written


def test_main_real_model_flag_overrides_the_environment(tmp_path, monkeypatch):
    real_env(monkeypatch, LLM_MODEL="<model name>")
    seen = []
    monkeypatch.setattr(
        evaluate, "build_llm", lambda s: seen.append(s.llm_model) or MockLLM("ok", s.llm_model)
    )
    monkeypatch.setattr(evaluate, "RateLimiter", NoWait)
    assert (
        evaluate.main(
            [
                "--real",
                "--model",
                "openai/gpt-oss-20b",
                "--limit",
                "1",
                "--out",
                str(tmp_path / "r.md"),
            ]
        )
        == 0
    )
    assert seen == ["openai/gpt-oss-20b"]


def test_dataclass_helpers():
    assert evaluate.TypeScore().precision is None and evaluate.TypeScore().recall is None
    s = dataclasses.replace(evaluate.TypeScore(), tp=3, fp=1, fn=1)
    assert (s.precision, s.recall) == (0.75, 0.75)
    assert evaluate._pct(0, 0) == "n/a" and evaluate._pct(1, 4) == "25.0%"
