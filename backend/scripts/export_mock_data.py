"""Export mock data and draft TypeScript types for the frontend.

    python -m scripts.export_mock_data            # write docs/frontend-mock/*.json and types.ts
    python -m scripts.export_mock_data --check    # exit 1 if the committed files are out of date

Everything is built from the real contract models (backend/app/contracts/models.py), the
real AI workflow (backend/app/ai) and the mock LLM. No network, no database. Nothing is
hand-typed JSON: the JSON comes from model_dump, and types.ts is generated from the models.

Output is reproducible: ids are derived from names (uuid5) and audit timestamps are fixed,
so the files only change when the contracts or the workflow change.

The shapes CaseListItem, CaseDetail and DecisionRequest below are PROPOSED API shapes for
the frontend. They are not in models.py and are not contracts. TODO(Raman): decide the real
response shapes. README.md in the output folder is written by hand and is not touched here.
"""

from __future__ import annotations

import argparse
import json
import sys
import types
import typing
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, get_args, get_origin

from pydantic import BaseModel

from app.ai.llm import MockLLM
from app.ai.retrieval import InMemoryRetriever
from app.ai.settings import AISettings
from app.ai.workflow import run_workflow
from app.contracts import (
    AuditEvent,
    Decision,
    DetectedException,
    ExceptionType,
    PolicyConfig,
    Proposal,
    Punch,
    PunchType,
    Shift,
    Timecard,
)
from app.contracts import models as contract_models
from app.domain.rules import (
    RULE_DAILY_MAX,
    RULE_MEAL_BREAK,
    RULE_MISSED_OUT,
    RULE_OVERLAP,
    RULE_WEEKLY_OT,
    detect_exceptions,
)
from app.validator import validate_proposal

from .smoke_llm import IST, fixture_case

BACKEND_DIR = Path(__file__).resolve().parents[1]
DEFAULT_OUT = BACKEND_DIR.parent / "docs" / "frontend-mock"
NAMESPACE = uuid.UUID("5c1c7b0e-0000-4000-8000-00000000d47a")  # fixed, so ids are reproducible
BASE_TIME = datetime(2026, 3, 11, 4, 0, tzinfo=UTC)
MANAGER = "mgr-0001"


# ---------------------------------------------------------------- proposed API shapes
# NOT contracts. They only wrap contract models into what a screen needs.


class CaseListItem(BaseModel):
    """One row of the dashboard list."""

    exception: DetectedException
    proposal: Proposal | None = None


class CaseDetail(BaseModel):
    """Everything the detail screen shows for one exception."""

    timecard: Timecard
    exception: DetectedException
    proposal: Proposal | None = None
    decision: Decision | None = None
    audit_events: list[AuditEvent]


class DecisionRequest(BaseModel):
    """Body of the approve / reject call."""

    decision: typing.Literal["APPROVE", "REJECT"]
    manager_id: str
    comment: str | None = None


# ---------------------------------------------------------------- building blocks


def uid(*parts: str) -> str:
    return str(uuid.uuid5(NAMESPACE, ":".join(parts)))


def ist(day: int, hour: int, minute: int = 0) -> datetime:
    """A March 2026 wall-clock time in Asia/Kolkata, as UTC (what the API sends)."""
    return datetime(2026, 3, day, hour, minute, tzinfo=IST).astimezone(UTC)


def punch(key: str, n: str, emp: str, kind: PunchType, when: datetime) -> Punch:
    return Punch(id=uid(key, "punch", n), employee_id=emp, type=kind, timestamp=when)


def shift(key: str, n: str, emp: str, start: datetime, end: datetime, loc: str) -> Shift:
    return Shift(id=uid(key, "shift", n), employee_id=emp, start=start, end=end, location_id=loc)


def derive(
    timecards: list[Timecard], kind: ExceptionType, rule: str
) -> tuple[Timecard, DetectedException]:
    """Run the real detectors over these timecards and return the exception they find, with
    the timecard it belongs to. Nothing about the exception is typed by hand."""
    exc = next(
        e for e in detect_exceptions(timecards) if e.type is kind and e.evidence.get("rule") == rule
    )
    return next(t for t in timecards if t.id == exc.timecard_id), exc


def missed_punch(key: str, day: int, notes: str | None):
    emp = uid(key, "employee")
    sh = shift(key, "1", emp, ist(day, 9), ist(day, 18), "loc-pune-01")
    tc = Timecard(
        id=uid(key, "timecard"),
        employee_id=emp,
        work_date=date(2026, 3, day),
        punches=[punch(key, "in", emp, PunchType.IN, ist(day, 9, 2))],
        shifts=[sh],
        notes=notes,
    )
    return derive([tc], ExceptionType.MISSED_PUNCH, RULE_MISSED_OUT)


def overtime_week(key: str, thursday: int, friday_hours: int = 8):
    """Monday to Thursday worked (8 paid hours a day) plus a Friday shift still to come."""
    emp = uid(key, "employee")
    week = []
    for d in range(thursday - 3, thursday + 1):
        week.append(
            Timecard(
                id=uid(key, "timecard", str(d)),
                employee_id=emp,
                work_date=date(2026, 3, d),
                punches=[
                    punch(key, f"in{d}", emp, PunchType.IN, ist(d, 9)),
                    punch(key, f"bs{d}", emp, PunchType.BREAK_START, ist(d, 13)),
                    punch(key, f"be{d}", emp, PunchType.BREAK_END, ist(d, 13, 30)),
                    punch(key, f"out{d}", emp, PunchType.OUT, ist(d, 17, 30)),
                ],
                shifts=[shift(key, str(d), emp, ist(d, 9), ist(d, 17, 30), "loc-pune-01")],
                notes=None,
            )
        )
    friday = thursday + 1
    week.append(
        Timecard(
            id=uid(key, "timecard", str(friday)),
            employee_id=emp,
            work_date=date(2026, 3, friday),
            punches=[],
            shifts=[
                shift(key, "fri", emp, ist(friday, 9), ist(friday, 9 + friday_hours), "loc-pune-01")
            ],
            notes=None,
        )
    )
    return derive(week, ExceptionType.OVERTIME_RISK, RULE_WEEKLY_OT)


def schedule_conflict(key: str, day: int):
    emp = uid(key, "employee")
    tc = Timecard(
        id=uid(key, "timecard"),
        employee_id=emp,
        work_date=date(2026, 3, day),
        punches=[],
        shifts=[
            shift(key, "1", emp, ist(day, 9), ist(day, 17), "loc-pune-01"),
            shift(key, "2", emp, ist(day, 15), ist(day, 21), "loc-mumbai-02"),
        ],
        notes=None,
    )
    return derive([tc], ExceptionType.SCHEDULE_CONFLICT, RULE_OVERLAP)


def missing_break(key: str, day: int, notes: str | None):
    emp = uid(key, "employee")
    tc = Timecard(
        id=uid(key, "timecard"),
        employee_id=emp,
        work_date=date(2026, 3, day),
        punches=[
            punch(key, "in", emp, PunchType.IN, ist(day, 9)),
            punch(key, "out", emp, PunchType.OUT, ist(day, 17, 30)),
        ],
        shifts=[shift(key, "1", emp, ist(day, 9), ist(day, 17, 30), "loc-pune-01")],
        notes=notes,
    )
    return derive([tc], ExceptionType.POLICY_BREACH, RULE_MEAL_BREAK)


def long_day(key: str, day: int, notes: str | None):
    """A day over 12 hours. The policy has no automatic fix for this: manager review."""
    emp = uid(key, "employee")
    tc = Timecard(
        id=uid(key, "timecard"),
        employee_id=emp,
        work_date=date(2026, 3, day),
        punches=[
            punch(key, "in", emp, PunchType.IN, ist(day, 8)),
            punch(key, "out", emp, PunchType.OUT, ist(day, 21, 30)),
        ],
        shifts=[shift(key, "1", emp, ist(day, 8), ist(day, 16), "loc-pune-01")],
        notes=notes,
    )
    return derive([tc], ExceptionType.POLICY_BREACH, RULE_DAILY_MAX)


# ---------------------------------------------------------------- the cases


@dataclass(frozen=True)
class Spec:
    key: str
    mock_mode: str  # ok | invalid_json | low_confidence (decides LLM vs fallback vs escalate)
    decision: str | None  # None | "APPROVE" | "REJECT"
    comment: str | None
    detail_file: str | None  # case_detail_<this>.json, if this case gets a full example


SPECS = [
    Spec("missed-punch-llm", "ok", None, None, "missed_punch_llm"),
    Spec("overtime-risk-fallback", "invalid_json", None, None, "overtime_risk_fallback"),
    Spec("schedule-conflict-llm", "ok", None, None, None),
    Spec("break-approved", "ok", "APPROVE", "Employee confirmed no break was taken.", None),
    Spec("missed-punch-fallback-approved", "invalid_json", "APPROVE", "Matches the roster.", None),
    Spec("overtime-risk-rejected", "ok", "REJECT", "Keep the shift, I will talk to them.", None),
    Spec("long-day-escalated", "invalid_json", None, None, "escalated"),
    Spec("schedule-conflict-low-confidence", "low_confidence", None, None, None),
]


def make_input(spec: Spec) -> tuple[Timecard, DetectedException]:
    k = spec.key
    if k == "missed-punch-llm":
        exc, tc = fixture_case()  # the real fixtures
        return tc, exc
    if k == "overtime-risk-fallback":
        return overtime_week(k, 12)
    if k == "schedule-conflict-llm":
        return schedule_conflict(k, 13)
    if k == "break-approved":
        return missing_break(k, 9, "Busy day, skipped lunch.")
    if k == "missed-punch-fallback-approved":
        return missed_punch(k, 10, None)
    if k == "overtime-risk-rejected":
        return overtime_week(k, 5, friday_hours=10)  # 42 projected hours
    if k == "long-day-escalated":
        return long_day(k, 6, "Stayed late to finish the stock count.")
    if k == "schedule-conflict-low-confidence":
        return schedule_conflict(k, 4)
    raise KeyError(k)


def build_case(index: int, spec: Spec) -> CaseDetail:
    tc, exc = make_input(spec)
    proposal, events = run_workflow(
        exc,
        tc,
        retriever=InMemoryRetriever(),
        llm=MockLLM(spec.mock_mode),
        validator=validate_proposal,
        cfg=PolicyConfig(),
        settings=AISettings(),
    )
    # The workflow uses random ids and the current time. Replace them so output is stable.
    proposal_id = uid(spec.key, "proposal")
    start = BASE_TIME + timedelta(minutes=index)
    fixed: list[AuditEvent] = []
    for n, e in enumerate(events):
        payload = dict(e.payload)
        if "proposal_id" in payload:
            payload["proposal_id"] = proposal_id
        fixed.append(
            e.model_copy(
                update={
                    "id": uid(spec.key, "event", str(n)),
                    "ts": start + timedelta(seconds=n),
                    "payload": payload,
                }
            )
        )
    proposal = proposal.model_copy(update={"id": proposal_id})

    decision = None
    if proposal.status == "ESCALATED":
        exc_status = "ESCALATED"
    elif spec.decision is None:
        exc_status = "PROPOSED"
    else:
        approved = spec.decision == "APPROVE"
        decided_at = start + timedelta(minutes=30)
        decision = Decision(
            id=uid(spec.key, "decision"),
            proposal_id=proposal.id,
            decision=spec.decision,  # type: ignore[arg-type]
            manager_id=MANAGER,
            comment=spec.comment,
            decided_at=decided_at,
        )
        proposal = proposal.model_copy(update={"status": "APPROVED" if approved else "REJECTED"})
        # TODO(Raman): what status a rejected exception returns to is not defined in CLAUDE.md.
        exc_status = "RESOLVED" if approved else "OPEN"
        fixed.append(
            AuditEvent(
                id=uid(spec.key, "event", "decision"),
                ts=decided_at,
                actor=MANAGER,
                event_type="DECISION_APPROVED" if approved else "DECISION_REJECTED",
                entity_type="proposal",
                entity_id=proposal.id,
                payload={
                    "decision_id": decision.id,
                    "exception_id": exc.id,
                    "comment": spec.comment,
                },
            )
        )
    exc = exc.model_copy(update={"status": exc_status})
    return CaseDetail(
        timecard=tc, exception=exc, proposal=proposal, decision=decision, audit_events=fixed
    )


def build_cases() -> list[tuple[Spec, CaseDetail]]:
    return [(spec, build_case(i, spec)) for i, spec in enumerate(SPECS)]


# ---------------------------------------------------------------- TypeScript generation

HINTS = {
    "Timecard.notes": "UNTRUSTED free text: show as plain text, never as HTML",
    "DetectedException.evidence": "keys differ per exception type",
    "DetectedException.baseline_fix": "the rule-based fix; null when the policy has none",
    "FieldChange.field": "a label such as 'punch.OUT', not a fixed list",
    "Proposal.rationale": "plain language",
    "Proposal.model": "null for rule-based fixes and escalations",
    "Proposal.validation": "null for escalations",
    "AuditEvent.actor": "'system', 'llm' or a manager id",
    "AuditEvent.payload": "keys differ per event_type",
}


def ts_type(tp: Any) -> str:
    origin = get_origin(tp)
    if origin in (typing.Union, types.UnionType):
        args = get_args(tp)
        rest = [a for a in args if a is not type(None)]
        text = " | ".join(ts_type(a) for a in rest)
        return f"{text} | null" if len(rest) != len(args) else text
    if origin is Literal:
        return " | ".join(f"'{a}'" for a in get_args(tp))
    if origin is list:
        inner = ts_type(get_args(tp)[0])
        return f"({inner})[]" if " | " in inner else f"{inner}[]"
    if origin is dict:
        value = get_args(tp)[1]
        return "Record<string, unknown>" if value is Any else f"Record<string, {ts_type(value)}>"
    if tp is Any:
        return "unknown"
    if tp is bool:
        return "boolean"
    if tp in (int, float):
        return "number"
    if tp in (str, datetime, date):
        return "string"  # datetime and date travel as ISO-8601 strings
    if isinstance(tp, type) and issubclass(tp, (Enum, BaseModel)):
        return tp.__name__
    raise TypeError(f"no TypeScript mapping for {tp!r}")


def constraint_note(field: Any) -> str | None:
    """Turn pydantic constraints (max_length, ge, le) into a short comment."""
    notes = []
    lows = [m.ge for m in field.metadata if getattr(m, "ge", None) is not None]
    highs = [m.le for m in field.metadata if getattr(m, "le", None) is not None]
    for m in field.metadata:
        if getattr(m, "max_length", None) is not None:
            notes.append(f"max {m.max_length} characters")
    if lows and highs:
        notes.append(f"{lows[0]:g} to {highs[0]:g}")
    return ", ".join(notes) or None


def ts_enum(enum_cls: type[Enum]) -> str:
    values = [f"'{m.value}'" for m in enum_cls]
    one_line = f"export type {enum_cls.__name__} = {' | '.join(values)}"
    if len(one_line) <= 90:
        return one_line
    return f"export type {enum_cls.__name__} =\n" + "\n".join(f"  | {v}" for v in values)


def ts_interface(model: type[BaseModel]) -> str:
    lines = [f"export interface {model.__name__} {{"]
    for name, field in model.model_fields.items():
        tp = field.annotation
        nullable = type(None) in get_args(tp)
        parts = [constraint_note(field), HINTS.get(f"{model.__name__}.{name}")]
        note = "; ".join(p for p in parts if p)
        suffix = f" // {note}" if note else ""
        lines.append(f"  {name}{'?' if nullable else ''}: {ts_type(tp)}{suffix}")
    lines.append("}")
    return "\n".join(lines)


def contract_classes() -> list[type]:
    """Enums and models defined in models.py, in source order."""
    return [
        obj
        for obj in vars(contract_models).values()
        if isinstance(obj, type)
        and obj.__module__ == contract_models.__name__
        and issubclass(obj, (Enum, BaseModel))
    ]


def render_types() -> str:
    out = [
        "// DRAFT for Priyanshu to copy into frontend/. This is NOT a contract.",
        "// The source of truth is backend/app/contracts/models.py.",
        "// If they disagree, models.py wins.",
        "// Generated by backend/scripts/export_mock_data.py from those models.",
        "// Do not edit by hand.",
        "//",
        "// JSON rules: keys are snake_case, ids are UUID strings, datetimes are ISO-8601 strings",
        "// in UTC (show them in Asia/Kolkata), dates are 'YYYY-MM-DD'.",
        "// 'name?: T | null' means the backend may send null: do not assume the field is there.",
        "",
    ]
    for cls in contract_classes():
        out.append(ts_enum(cls) if issubclass(cls, Enum) else ts_interface(cls))
        out.append("")
    out += [
        "// ---------------------------------------------------------------------------",
        "// PROPOSED API shapes. Not in models.py. The backend owner may change them.",
        "// ---------------------------------------------------------------------------",
        "",
    ]
    for model in (CaseListItem, CaseDetail, DecisionRequest):
        out.append(ts_interface(model))
        out.append("")
    return "\n".join(out)


# ---------------------------------------------------------------- output


def dump(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def build_all() -> dict[str, str]:
    """File name -> exact file content."""
    cases = build_cases()
    files = {
        "cases.json": dump(
            [
                CaseListItem(exception=d.exception, proposal=d.proposal).model_dump(mode="json")
                for _, d in cases
            ]
        ),
        "types.ts": render_types(),
    }
    for spec, detail in cases:
        if spec.detail_file:
            files[f"case_detail_{spec.detail_file}.json"] = dump(detail.model_dump(mode="json"))
    return files


def write(out_dir: Path) -> list[str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    files = build_all()
    for name, text in files.items():
        (out_dir / name).write_text(text)
    return sorted(files)


def out_of_date(out_dir: Path) -> list[str]:
    stale = []
    for name, text in build_all().items():
        path = out_dir / name
        if not path.exists() or path.read_text() != text:
            stale.append(name)
    return stale


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT, help="output folder")
    ap.add_argument("--check", action="store_true", help="only check the files are up to date")
    args = ap.parse_args(argv)
    if args.check:
        stale = out_of_date(args.out)
        if stale:
            print("out of date: " + ", ".join(stale), file=sys.stderr)
            return 1
        print("up to date")
        return 0
    print("wrote " + ", ".join(write(args.out)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
