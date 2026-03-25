"""Result-type-specific validators for the safety pipeline."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from controller.models import (
    AgentResult,
    Thread,
    ResultType,
    ReversibilityLevel,
)

logger = logging.getLogger(__name__)


@dataclass
class ValidationOutcome:
    approved: bool
    reason: str | None = None


@runtime_checkable
class ResultValidator(Protocol):
    async def validate(self, result: AgentResult, thread: Thread) -> ValidationOutcome: ...


class PRValidator:
    """Validates CODE_CHANGE results (the original behavior)."""

    async def validate(self, result: AgentResult, thread: Thread) -> ValidationOutcome:
        if result.exit_code != 0:
            return ValidationOutcome(approved=True)
        if result.commit_count == 0:
            return ValidationOutcome(
                approved=False,
                reason="Agent produced no changes (0 commits)",
            )
        return ValidationOutcome(approved=True)


class ReportValidator:
    """Validates ANALYSIS results — must produce at least one artifact."""

    async def validate(self, result: AgentResult, thread: Thread) -> ValidationOutcome:
        if not result.artifacts:
            return ValidationOutcome(
                approved=False,
                reason="Report produced no artifacts — expected at least a summary",
            )
        return ValidationOutcome(approved=True)


_VALIDATORS: dict[ResultType, ResultValidator] = {
    ResultType.PULL_REQUEST: PRValidator(),
    ResultType.REPORT: ReportValidator(),
}

_FALLBACK_VALIDATOR = PRValidator()


def get_validator(result_type: ResultType) -> ResultValidator:
    """Get the validator for a given result type."""
    return _VALIDATORS.get(result_type, _FALLBACK_VALIDATOR)


REVERSIBILITY: dict[ResultType, ReversibilityLevel] = {
    ResultType.PULL_REQUEST: ReversibilityLevel.TRIVIAL,
    ResultType.REPORT: ReversibilityLevel.TRIVIAL,
    ResultType.FILE_ARTIFACT: ReversibilityLevel.TRIVIAL,
    ResultType.DB_ROWS: ReversibilityLevel.POSSIBLE,
    ResultType.API_RESPONSE: ReversibilityLevel.DIFFICULT,
}
