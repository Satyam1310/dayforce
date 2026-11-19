"""The evaluation must FAIL when something regresses. Each test changes one rule or guard
(with monkeypatch, so nothing in the repo is edited) and checks the golden set notices."""

import pytest

from app.ai import prompts, workflow
from app.ai.retrieval import _QUERY_BY_TYPE
from app.contracts import PolicyConfig
from scripts import evaluate
from tests.golden.loader import load_cases

CASES = load_cases()


def run():
    return evaluate.run_mock_evaluation(CASES)


def failing(res):
    return {r.case_id for r in res.rows if not r.passed}


def test_the_unchanged_code_passes():
    assert failing(run()) == set()


def test_a_changed_overtime_threshold_is_caught(monkeypatch):
    # "8 hours" quietly becomes "7.5 hours"
    monkeypatch.setattr(evaluate, "CFG", PolicyConfig(daily_ot_hours=7.5))
    res = run()
    assert "ot01" in failing(res) and not res.all_passed
    assert res.clean_false_positives > 0
    assert res.by_type["OVERTIME_RISK"].precision < 1.0


def test_a_changed_rest_rule_is_caught(monkeypatch):
    monkeypatch.setattr(evaluate, "CFG", PolicyConfig(min_rest_hours=9))
    assert {"pb06", "mp09"} & failing(run())


def test_a_changed_meal_break_rule_is_caught(monkeypatch):
    monkeypatch.setattr(evaluate, "CFG", PolicyConfig(break_required_after_hours=5.5))
    res = run()
    assert "pb01" in failing(res) and res.clean_false_positives > 0


def test_a_validator_that_accepts_everything_is_caught(monkeypatch):
    from app.contracts import ValidationResult

    def accept_all(timecard, exc, fix, cfg):
        return ValidationResult(passed=True, violations=[])

    monkeypatch.setattr(evaluate, "validate_proposal", accept_all)
    monkeypatch.setattr(workflow, "validate_proposal", accept_all)
    res = run()
    assert res.bad_accepted and res.bad_rejected < res.bad_fixes
    assert res.attacks_followed > 0  # the obedient model's attack now gets through
    assert not res.all_passed


def test_notes_that_are_not_escaped_are_caught(monkeypatch):
    monkeypatch.setattr(
        prompts, "sanitize_notes", lambda notes: f"<untrusted_notes>{notes or ''}</untrusted_notes>"
    )
    res = run()
    assert res.tag_breakouts > 0 or res.long_notes_cut > 0
    assert "inj04" in failing(res)


def test_a_retrieval_query_that_loses_the_right_clause_is_caught(monkeypatch):
    # the bug that the first evaluation found: a query by exception type only
    monkeypatch.setattr(workflow, "build_query", lambda exc: _QUERY_BY_TYPE[exc.type])
    res = run()
    assert not res.all_passed
    assert {"ot02", "ot03"} <= failing(res)


@pytest.mark.parametrize("mode", ["ok", "low_confidence"])
def test_the_report_names_the_failing_case(monkeypatch, mode):
    monkeypatch.setattr(evaluate, "CFG", PolicyConfig(daily_ot_hours=7.5))
    text = evaluate.render_mock_section(run())
    assert "**FAIL**" in text and "### Problems" in text and "ot01" in text
