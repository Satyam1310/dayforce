"""The frontend mock data must always load back through the contract models.

These tests read the committed files in docs/frontend-mock/, so the data cannot drift from
backend/app/contracts/models.py. They use the mock LLM only, so there is no network.
"""

import json
import re
import typing
from enum import Enum

import pytest

from app.contracts import models
from app.contracts.models import (
    AuditEvent,
    DetectedException,
    ExceptionType,
    Proposal,
    ProposalSource,
    Severity,
)
from scripts import export_mock_data as exp

OUT = exp.DEFAULT_OUT
DETAIL_FILES = [
    "case_detail_missed_punch_llm.json",
    "case_detail_overtime_risk_fallback.json",
    "case_detail_escalated.json",
]


def load(name):
    return json.loads((OUT / name).read_text())


def literal_values(model, field):
    return set(typing.get_args(model.model_fields[field].annotation))


# ---------------------------------------------------------------- committed files vs models


def test_cases_json_loads_through_the_models():
    items = [exp.CaseListItem.model_validate(x) for x in load("cases.json")]
    assert len(items) == 8


@pytest.mark.parametrize("name", DETAIL_FILES)
def test_detail_files_load_through_the_models(name):
    detail = exp.CaseDetail.model_validate(load(name))
    assert detail.audit_events
    assert detail.exception.timecard_id == detail.timecard.id
    assert detail.exception.employee_id == detail.timecard.employee_id
    assert detail.proposal.exception_id == detail.exception.id
    assert all(
        e.entity_id in (detail.exception.id, detail.proposal.id) for e in detail.audit_events
    )


def test_every_json_file_in_the_folder_is_covered():
    names = {p.name for p in OUT.glob("*.json")}
    assert names == {"cases.json", *DETAIL_FILES}


def test_files_are_up_to_date_with_the_models_and_the_workflow():
    """If this fails, run: cd backend && python -m scripts.export_mock_data"""
    assert exp.out_of_date(OUT) == []


def test_output_is_reproducible():
    assert exp.build_all() == exp.build_all()


def test_json_round_trips_without_loss():
    for item in load("cases.json"):
        parsed = exp.CaseListItem.model_validate(item)
        assert json.loads(parsed.model_dump_json()) == item


# ---------------------------------------------------------------- coverage of the contracts


def test_cases_cover_every_type_status_and_source():
    items = [exp.CaseListItem.model_validate(x) for x in load("cases.json")]
    assert {i.exception.type for i in items} == set(ExceptionType)
    assert {i.exception.severity for i in items} == set(Severity)
    assert {i.exception.status for i in items} == literal_values(DetectedException, "status")
    assert {i.proposal.status for i in items} == literal_values(Proposal, "status")
    assert {i.proposal.source for i in items} == set(ProposalSource)


def test_decisions_and_status_are_consistent():
    for _, d in exp.build_cases():
        p, e = d.proposal, d.exception
        if p.status == "APPROVED":
            assert d.decision.decision == "APPROVE" and e.status == "RESOLVED"
        elif p.status == "REJECTED":
            assert d.decision.decision == "REJECT" and e.status == "OPEN"
        elif p.status == "ESCALATED":
            assert d.decision is None and e.status == "ESCALATED"
            assert p.fix.action.value == "FLAG_FOR_MANAGER" and p.fix.changes == []
        else:
            assert d.decision is None and e.status == "PROPOSED"
        if d.decision:
            assert d.audit_events[-1].event_type.startswith("DECISION_")


def test_every_citation_was_a_retrieved_clause():
    for _, d in exp.build_cases():
        retrieved = {
            c["clause_id"]
            for e in d.audit_events
            if e.event_type == "POLICY_RETRIEVED"
            for c in e.payload["clauses"]
        }
        assert {c.clause_id for c in d.proposal.citations} <= retrieved


def test_ids_are_uuids_and_unique():
    import uuid

    for _, d in exp.build_cases():
        for value in (d.timecard.id, d.exception.id, d.proposal.id, d.timecard.employee_id):
            uuid.UUID(value)
        ids = [e.id for e in d.audit_events]
        assert len(ids) == len(set(ids))


# ---------------------------------------------------------------- types.ts


def test_types_ts_is_generated_from_the_models_and_up_to_date():
    assert (OUT / "types.ts").read_text() == exp.render_types()


def test_types_ts_has_every_model_and_every_enum_value():
    text = (OUT / "types.ts").read_text()
    for cls in exp.contract_classes():
        pattern = rf"export (interface|type) {cls.__name__}\b"
        assert re.search(pattern, text), cls.__name__
        if issubclass(cls, Enum):
            for member in cls:
                assert f"'{member.value}'" in text
        else:
            for name in cls.model_fields:
                assert re.search(rf"^\s+{name}\??: ", text, re.M), f"{cls.__name__}.{name}"
    for model in (models.Proposal, models.DetectedException):
        for field in ("status",):
            for value in literal_values(model, field):
                assert f"'{value}'" in text


def test_types_ts_marks_optional_fields_and_constraints():
    text = (OUT / "types.ts").read_text()
    assert "notes?: string | null" in text
    assert "baseline_fix?: Fix | null" in text
    assert "clause_id?: string | null" in text
    assert "confidence: number // 0 to 1" in text
    assert "max 400 characters" in text
    assert "evidence: Record<string, unknown>" in text
    assert text.startswith("// DRAFT for Priyanshu to copy into frontend/. This is NOT a contract.")
    assert "PROPOSED API shapes" in text


def test_ts_type_rejects_unknown_types():
    with pytest.raises(TypeError):
        exp.ts_type(bytes)


# ---------------------------------------------------------------- README guards


def test_readme_mentions_every_enum_value_and_event_type():
    text = (OUT / "README.md").read_text()
    for cls in exp.contract_classes():
        if issubclass(cls, Enum):
            for member in cls:
                assert member.value in text, member.value
    for model, field in ((DetectedException, "status"), (Proposal, "status")):
        for value in literal_values(model, field):
            assert value in text, value
    seen = {e.event_type for _, d in exp.build_cases() for e in d.audit_events}
    for event_type in seen:
        assert event_type in text, event_type


def test_readme_case_table_matches_the_data():
    text = (OUT / "README.md").read_text()
    for spec, d in exp.build_cases():
        row = (
            f"| {d.exception.type.value} | {d.exception.severity.value} | {d.exception.status} "
            f"| {d.proposal.status} | {d.proposal.source.value} |"
        )
        assert row in text, f"{spec.key}: {row}"


def test_readme_marks_proposals_as_not_final():
    text = " ".join((OUT / "README.md").read_text().split())  # ignore line wrapping
    assert "Nothing here is a contract" in text
    assert "Proposed, the backend owner (Raman) may change all of this" in text


# ---------------------------------------------------------------- script behaviour


def test_write_and_check_modes(tmp_path, capsys):
    assert exp.main(["--out", str(tmp_path)]) == 0
    assert {p.name for p in tmp_path.iterdir()} == {"cases.json", "types.ts", *DETAIL_FILES}
    assert exp.main(["--out", str(tmp_path), "--check"]) == 0
    (tmp_path / "cases.json").write_text("[]")
    assert exp.main(["--out", str(tmp_path), "--check"]) == 1
    assert "cases.json" in capsys.readouterr().err
    assert exp.out_of_date(tmp_path / "missing") != []


def test_audit_event_model_is_used_for_every_event():
    for _, d in exp.build_cases():
        for e in d.audit_events:
            AuditEvent.model_validate(e.model_dump())
