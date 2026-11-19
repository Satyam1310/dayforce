"""Test doubles for the AI unit tests."""

from app.contracts import DetectedException, Fix, PolicyConfig, Timecard, ValidationResult


def pass_validator(
    timecard: Timecard, exc: DetectedException, fix: Fix, cfg: PolicyConfig
) -> ValidationResult:
    """A validator that accepts everything. Only for tests that exercise the workflow's own
    logic (retries, fallback, escalation) and must not depend on the real rules."""
    return ValidationResult(passed=True, violations=[])
