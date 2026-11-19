"""Failure injection: break the LLM in each way the mock can, and check the outcome is safe.

Safe means: no crash, no unvalidated fix, nothing applied without a manager, the failure is in
the audit trail and in /metrics, and the manager still gets a rule-based fix or an escalation.
"""

import pytest

from tests.sample_timecards import missed_punch

MANAGER = {"manager_id": "mgr-system"}


def run_case(api):
    tc = missed_punch()
    case_id = api.analyse(tc)["cases"][0]["exception"]["id"]
    return tc, case_id, api.propose(case_id)


def assert_safe_fallback(api, tc, case_id, proposal, expected_audit):
    assert proposal["source"] == "RULES_FALLBACK"
    assert proposal["status"] == "PENDING_REVIEW"
    assert proposal["model"] is None
    assert proposal["validation"]["passed"] is True
    assert proposal["fix"]["action"] == "ADD_PUNCH"  # the detector's own fix
    detail = api.case(case_id)
    types = [e["event_type"] for e in detail["audit_events"]]
    assert expected_audit in types and "FALLBACK_USED" in types
    assert [p["type"] for p in api.timecard(tc.employee_id)["punches"]] == ["IN"]  # not applied
    # the manager can still approve it, and only then does the timecard change
    done = api.http.post(f"/api/cases/{case_id}/approve", json=MANAGER)
    assert done.status_code == 200
    assert [p["type"] for p in done.json()["timecard"]["punches"]] == ["IN", "OUT"]


def test_llm_timeout_falls_back_to_the_rule_based_fix(start_api):
    api = start_api("timeout")
    before = api.metric("llm_errors_total", kind="timeout")
    tc, case_id, proposal = run_case(api)
    assert_safe_fallback(api, tc, case_id, proposal, "LLM_ERROR")
    assert api.metric("llm_errors_total", kind="timeout") == before + 1
    assert (
        api.metric("proposals_created_total", source="RULES_FALLBACK", status="PENDING_REVIEW") >= 1
    )


def test_provider_429_falls_back_to_the_rule_based_fix(start_api):
    api = start_api("rate_limited")
    before = api.metric("llm_errors_total", kind="provider_429")
    tc, case_id, proposal = run_case(api)
    assert_safe_fallback(api, tc, case_id, proposal, "LLM_ERROR")
    assert api.metric("llm_errors_total", kind="provider_429") == before + 1


def test_invalid_json_is_retried_once_then_falls_back(start_api):
    api = start_api("invalid_json")
    tc, case_id, proposal = run_case(api)
    assert_safe_fallback(api, tc, case_id, proposal, "LLM_INVALID_OUTPUT")
    events = api.case(case_id)["audit_events"]
    assert [e["event_type"] for e in events].count("LLM_INVALID_OUTPUT") == 2  # first try + retry
    assert "RETRY_BUDGET_EXHAUSTED" in [e["event_type"] for e in events]


def test_an_invented_citation_is_rejected_and_never_shown(start_api):
    api = start_api("bad_citation")
    tc, case_id, proposal = run_case(api)
    assert_safe_fallback(api, tc, case_id, proposal, "CITATION_REJECTED")
    assert all(c["clause_id"] != "POL-TA-99" for c in proposal["citations"])
    assert [c["clause_id"] for c in proposal["citations"]] == ["POL-TA-01"]


def test_low_confidence_escalates_and_the_case_cannot_be_approved(start_api):
    api = start_api("low_confidence")
    before = api.metric("escalations_total", reason="LOW_CONFIDENCE")
    tc, case_id, proposal = run_case(api)
    assert proposal["status"] == "ESCALATED"
    assert proposal["fix"]["action"] == "FLAG_FOR_MANAGER" and proposal["fix"]["changes"] == []
    assert api.case(case_id)["exception"]["status"] == "ESCALATED"
    assert api.metric("escalations_total", reason="LOW_CONFIDENCE") == before + 1

    r = api.http.post(f"/api/cases/{case_id}/approve", json=MANAGER)
    assert r.status_code == 409 and r.json()["error"]["code"] == "CASE_ESCALATED"
    assert [p["type"] for p in api.timecard(tc.employee_id)["punches"]] == ["IN"]


@pytest.mark.parametrize("mode", ["timeout", "rate_limited", "invalid_json", "bad_citation"])
def test_no_failure_mode_produces_an_llm_sourced_proposal(start_api, mode):
    api = start_api(mode)
    _, _, proposal = run_case(api)
    assert proposal["source"] != "LLM"
