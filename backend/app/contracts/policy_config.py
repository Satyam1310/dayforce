"""Company policy numbers: one place, imported everywhere (CLAUDE.md sections 7 and 8)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class PolicyConfig(BaseModel):
    model_config = ConfigDict(frozen=True)

    daily_ot_hours: float = 8
    max_daily_hours: float = 12
    weekly_ot_hours: float = 40
    overtime_risk_threshold: float = 38
    break_required_after_hours: float = 6
    min_break_minutes: int = 30
    min_rest_hours: float = 10
    missed_punch_max_shift_hours: float = 12
    week_starts: str = "MONDAY"
    company_tz: str = "Asia/Kolkata"
