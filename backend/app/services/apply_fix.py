"""Apply an APPROVED fix to a timecard. Pure function: no I/O, returns a new Timecard.

Only called from the decision endpoint after a manager approves (CLAUDE.md section 2:
"Only a human decision changes a timecard").
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta

from app.contracts import Fix, FixAction, Punch, PunchType, Timecard


class FixNotApplicable(ValueError):
    """The fix cannot be applied to this timecard (unknown field, missing target...)."""


def _parse_ts(value: str | None) -> datetime:
    if not value:
        raise FixNotApplicable("missing timestamp in change.new")
    try:
        ts = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as e:
        raise FixNotApplicable(f"not an ISO date-time: {value!r}") from e
    if ts.tzinfo is None:
        raise FixNotApplicable(f"timestamp must include a timezone: {value!r}")
    return ts


def apply_fix(timecard: Timecard, fix: Fix) -> Timecard:
    tc = timecard.model_copy(deep=True)

    if fix.action in (FixAction.FLAG_FOR_MANAGER, FixAction.NO_CHANGE):
        return tc

    for change in fix.changes:
        if fix.action == FixAction.ADD_PUNCH:
            # field "punch.OUT" / "punch.IN" / ...; new = ISO timestamp
            prefix, _, kind = change.field.partition(".")
            if prefix != "punch" or kind not in PunchType.__members__:
                raise FixNotApplicable(f"ADD_PUNCH cannot use field {change.field!r}")
            tc.punches.append(
                Punch(
                    id=str(uuid.uuid4()),
                    employee_id=tc.employee_id,
                    type=PunchType(kind),
                    timestamp=_parse_ts(change.new),
                )
            )

        elif fix.action == FixAction.EDIT_PUNCH:
            # target_id = punch id; new = ISO timestamp
            punch = next((p for p in tc.punches if p.id == change.target_id), None)
            if punch is None:
                raise FixNotApplicable(f"punch {change.target_id} is not on this timecard")
            punch.timestamp = _parse_ts(change.new)

        elif fix.action == FixAction.DEDUCT_BREAK:
            # field "break_minutes", new = minutes. The contract has no break field, so the
            # deduction is recorded as a BREAK_START/BREAK_END pair centred in the worked span.
            try:
                minutes = int(change.new or "0") - int(change.old or "0")
            except ValueError as e:
                raise FixNotApplicable(f"break minutes must be numbers: {change}") from e
            if minutes <= 0:
                raise FixNotApplicable("break deduction must be positive")
            ins = [p.timestamp for p in tc.punches if p.type == PunchType.IN]
            outs = [p.timestamp for p in tc.punches if p.type == PunchType.OUT]
            if not ins or not outs:
                raise FixNotApplicable("need an IN and an OUT punch to place a break")
            start, end = min(ins), max(outs)
            break_start = start + (end - start) / 2 - timedelta(minutes=minutes / 2)
            for kind, ts in (
                (PunchType.BREAK_START, break_start),
                (PunchType.BREAK_END, break_start + timedelta(minutes=minutes)),
            ):
                tc.punches.append(
                    Punch(
                        id=str(uuid.uuid4()), employee_id=tc.employee_id, type=kind, timestamp=ts
                    )
                )

        elif fix.action == FixAction.REASSIGN_SHIFT:
            # target_id = shift id. Remove it from this employee's day if it is on this
            # timecard; a future shift (not on this timecard) changes nothing here.
            tc.shifts = [s for s in tc.shifts if s.id != change.target_id]

        else:  # pragma: no cover - every FixAction is handled above
            raise FixNotApplicable(f"unsupported action {fix.action}")

    tc.punches.sort(key=lambda p: p.timestamp)
    return tc
