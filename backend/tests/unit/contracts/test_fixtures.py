import json
from pathlib import Path

import pytest

from app.contracts import (
    AuditEvent,
    Decision,
    DetectedException,
    Proposal,
    Timecard,
    ValidationResult,
)

FIXTURES = Path(__file__).resolve().parents[3] / "app" / "contracts" / "fixtures"

CASES = [
    ("timecard.json", Timecard),
    ("detected_exception.json", DetectedException),
    ("proposal_pending_review.json", Proposal),
    ("proposal_escalated.json", Proposal),
    ("validation_result_passed.json", ValidationResult),
    ("validation_result_failed.json", ValidationResult),
    ("decision_approve.json", Decision),
    ("audit_event.json", AuditEvent),
]


def load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize(("name", "model"), CASES)
def test_fixture_validates(name, model):
    model.model_validate(load(name))


def test_every_fixture_file_is_covered():
    assert {p.name for p in FIXTURES.glob("*.json")} == {c[0] for c in CASES}


def test_fixtures_are_consistent():
    tc = Timecard.model_validate(load("timecard.json"))
    exc = DetectedException.model_validate(load("detected_exception.json"))
    pending = Proposal.model_validate(load("proposal_pending_review.json"))
    escalated = Proposal.model_validate(load("proposal_escalated.json"))
    decision = Decision.model_validate(load("decision_approve.json"))
    audit = AuditEvent.model_validate(load("audit_event.json"))
    assert exc.timecard_id == tc.id and exc.employee_id == tc.employee_id
    assert pending.exception_id == escalated.exception_id == exc.id
    assert decision.proposal_id == pending.id == audit.entity_id
    assert pending.citations[0].clause_id in exc.policy_refs
    assert pending.fix == exc.baseline_fix
    assert pending.validation is not None and pending.validation.passed
