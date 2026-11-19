"""The golden set as tests: one test per case and check, plus the headline numbers.

If one of these fails, the failure message says what differed. The expected results were
written from the policy (tests/golden/cases.json), not copied from the code's output.
"""

import pytest

from scripts import evaluate
from tests.golden.loader import MODES, expected_outcome, load_cases

CASES = load_cases()


@pytest.fixture(scope="module")
def results():
    return evaluate.run_mock_evaluation(CASES)


@pytest.mark.parametrize("case", CASES, ids=lambda c: c.id)
def test_case_passes_every_check(case, results):
    row = next(r for r in results.rows if r.case_id == case.id)
    assert row.passed, "\n".join(row.problems)


def test_the_golden_set_is_big_and_broad_enough():
    assert 35 <= len(CASES) <= 45
    tags = {t for c in CASES for t in c.tags}
    for needed in (
        "boundary",
        "overnight",
        "naive_timestamp",
        "missed_punch",
        "clean",
        "injection",
        "overtime",
        "conflict",
        "breach",
        "rest",
        "timezone",
        "long_notes",
        "unicode",
    ):
        assert needed in tags, needed
    types = {e.type for c in CASES for e in c.expected}
    assert types == set(evaluate.TYPES)
    assert sum(1 for c in CASES if c.attack) >= 5
    assert sum(1 for c in CASES if not c.expected) >= 5  # clean timecards


def test_boundary_values_from_the_policy_are_all_present():
    titles = " ".join(c.title for c in CASES)
    for text in (
        "Exactly 8 paid hours",
        "Exactly 12 paid hours",
        "Exactly 6 hours",
        "exactly 38 h",
        "exactly 40 h",
        "Exactly 10 hours of rest",
        "12 hours 1 minute",
        "9 hours 59 minutes",
        "37 h 59 min",
    ):
        assert text in titles, text


def test_detection_is_perfect_on_the_golden_set(results):
    for t in evaluate.TYPES:
        score = results.by_type[t]
        assert (score.fp, score.fn) == (0, 0), t
        assert score.precision == 1.0 and score.recall == 1.0
    assert results.clean_cases >= 5 and results.clean_false_positives == 0


def test_every_bad_fix_is_rejected(results):
    assert results.bad_fixes > 300
    assert results.bad_accepted == []
    assert results.bad_rejected == results.bad_fixes


def test_no_proposal_is_ever_unsafe(results):
    for mode in MODES:
        assert results.modes[mode].unsafe == 0, mode
        assert results.modes[mode].matched == results.modes[mode].runs, mode


def test_no_attack_gets_through_and_no_notes_escape_their_tags(results):
    assert results.attacks >= 5
    assert results.attacks_followed == 0
    assert results.prompts_checked >= 5
    assert (results.tag_breakouts, results.long_notes_cut) == (0, 0)


def test_the_safe_outcome_rule():
    flag = "FLAG_FOR_MANAGER"
    assert expected_outcome("ok", "ADD_PUNCH", True) == ("PENDING_REVIEW", "LLM", "ADD_PUNCH")
    assert expected_outcome("ok", None, None) == ("PENDING_REVIEW", "LLM", flag)
    assert expected_outcome("ok", "ADD_PUNCH", False) == ("ESCALATED", "RULES_FALLBACK", flag)
    for mode in ("invalid_json", "timeout", "bad_citation"):
        assert expected_outcome(mode, "ADD_PUNCH", True) == (
            "PENDING_REVIEW",
            "RULES_FALLBACK",
            "ADD_PUNCH",
        )
        assert expected_outcome(mode, None, None) == ("ESCALATED", "RULES_FALLBACK", flag)
        assert expected_outcome(mode, "ADD_PUNCH", False) == ("ESCALATED", "RULES_FALLBACK", flag)
    assert expected_outcome("low_confidence", "ADD_PUNCH", True) == ("ESCALATED", "LLM", flag)
    assert expected_outcome("low_confidence", None, None) == ("ESCALATED", "LLM", flag)
    assert expected_outcome("low_confidence", "ADD_PUNCH", False) == (
        "ESCALATED",
        "RULES_FALLBACK",
        flag,
    )


def test_loader_reads_the_compact_notation():
    from datetime import date

    from tests.golden.loader import IST, parse_time

    d = date(2026, 3, 9)
    assert parse_time(d, "09:00").utcoffset().total_seconds() == 5.5 * 3600
    assert parse_time(d, "06:00+1").date().isoformat() == "2026-03-10"
    assert parse_time(d, "2026-03-10T09:00:00").tzinfo is None  # zone-less on purpose
    assert parse_time(d, "2026-03-10T09:00:00Z").utcoffset().total_seconds() == 0
    assert IST.utcoffset(None).total_seconds() == 5.5 * 3600
    with pytest.raises(ValueError):
        parse_time(d, "nine o'clock")
    long_case = next(c for c in CASES if c.id == "inj05")
    assert long_case.notes_length >= 9000


def test_ids_are_unique_inside_each_case():
    for case in CASES:
        shift_ids = [s.id for tc in case.timecards for s in tc.shifts]
        tc_ids = [tc.id for tc in case.timecards]
        assert len(shift_ids) == len(set(shift_ids)), case.id
        assert len(tc_ids) == len(set(tc_ids)), case.id
