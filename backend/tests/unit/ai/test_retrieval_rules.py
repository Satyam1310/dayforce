"""Retrieval must return the clause each detector rule relies on.

Regression guard: the first version searched with one query per exception TYPE. For daily
overtime that ranked the weekly clause first and left POL-TA-03 below the minimum score, so a
correct citation was rejected as invented and the workflow fell back for no good reason. The
golden-set evaluation found it. These tests keep it fixed.
"""

import pytest

from app.ai import workflow
from app.ai.retrieval import InMemoryRetriever, build_query
from app.ai.settings import AISettings
from app.contracts import DetectedException
from app.domain.rules import detect_exceptions
from tests.golden.loader import load_cases

MIN_SCORE = AISettings().retrieval_min_score_memory


def exceptions_by_rule():
    seen = {}
    for case in load_cases():
        for exc in detect_exceptions(case.timecards):
            seen.setdefault(exc.evidence["rule"], exc)
    return seen


RULES = exceptions_by_rule()


def test_every_detector_rule_is_covered_by_the_golden_set():
    assert set(RULES) == {
        "MISSED_CLOCK_OUT",
        "MISSING_CLOCK_IN",
        "DAILY_OT",
        "WEEKLY_OT",
        "OVERLAPPING_SHIFTS",
        "MEAL_BREAK",
        "DAILY_MAX",
        "MIN_REST",
    }


@pytest.mark.parametrize("rule", sorted(RULES))
def test_the_clause_the_detector_cites_is_retrieved_above_the_minimum(rule):
    exc = RULES[rule]
    hits = {c.clause_id: c.score for c in InMemoryRetriever().retrieve(build_query(exc), k=3)}
    wanted = exc.policy_refs[0]
    assert wanted in hits, f"{rule}: {wanted} not in the top 3 {hits}"
    assert hits[wanted] >= MIN_SCORE, f"{rule}: {wanted} scored {hits[wanted]}"
    assert max(hits, key=hits.get) == wanted, f"{rule}: {wanted} is not the best match {hits}"


def test_daily_overtime_no_longer_loses_its_clause():
    exc = RULES["DAILY_OT"]
    scores = {c.clause_id: c.score for c in InMemoryRetriever().retrieve(build_query(exc), k=3)}
    assert scores["POL-TA-03"] >= 0.9  # it was 0.33 with the type-only query


def test_an_exception_without_a_rule_uses_the_type_query():
    exc = RULES["MISSED_CLOCK_OUT"]
    old_style = DetectedException.model_validate(
        {**exc.model_dump(mode="json"), "evidence": {"clock_in": "2026-03-10T03:32:00+00:00"}}
    )
    assert build_query(old_style).startswith("missed punch")
    assert [c.clause_id for c in InMemoryRetriever().retrieve(build_query(old_style))][
        0
    ] == "POL-TA-01"


def test_workflow_uses_the_rule_specific_query():
    assert workflow.build_query is build_query
