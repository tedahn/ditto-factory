"""Tests for result type validators."""
import pytest
from unittest.mock import AsyncMock
from controller.models import (
    AgentResult, ResultType, Thread, ThreadStatus,
    Artifact, ReversibilityLevel,
)
from controller.jobs.validators import (
    ValidationOutcome,
    PRValidator,
    ReportValidator,
    get_validator,
    REVERSIBILITY,
)


class TestValidationOutcome:
    def test_approved_outcome(self):
        outcome = ValidationOutcome(approved=True)
        assert outcome.approved is True
        assert outcome.reason is None

    def test_rejected_outcome(self):
        outcome = ValidationOutcome(approved=False, reason="no commits")
        assert outcome.approved is False
        assert outcome.reason == "no commits"


class TestPRValidator:
    @pytest.mark.asyncio
    async def test_approves_result_with_commits(self):
        validator = PRValidator()
        result = AgentResult(branch="df/abc/123", exit_code=0, commit_count=3)
        thread = Thread(
            id="t1", source="slack", source_ref={},
            repo_owner="org", repo_name="repo",
        )
        outcome = await validator.validate(result, thread)
        assert outcome.approved is True

    @pytest.mark.asyncio
    async def test_approves_failed_result(self):
        validator = PRValidator()
        result = AgentResult(branch="df/abc/123", exit_code=1, commit_count=0)
        thread = Thread(
            id="t1", source="slack", source_ref={},
            repo_owner="org", repo_name="repo",
        )
        outcome = await validator.validate(result, thread)
        assert outcome.approved is True

    @pytest.mark.asyncio
    async def test_flags_empty_result_for_retry(self):
        validator = PRValidator()
        result = AgentResult(branch="df/abc/123", exit_code=0, commit_count=0)
        thread = Thread(
            id="t1", source="slack", source_ref={},
            repo_owner="org", repo_name="repo",
        )
        outcome = await validator.validate(result, thread)
        assert outcome.approved is False
        assert "no changes" in outcome.reason.lower()


class TestReportValidator:
    @pytest.mark.asyncio
    async def test_approves_report_with_artifacts(self):
        validator = ReportValidator()
        result = AgentResult(
            branch="", exit_code=0, commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[
                Artifact(result_type=ResultType.REPORT, location="inline",
                         metadata={"summary": "analysis complete"})
            ],
        )
        thread = Thread(
            id="t1", source="slack", source_ref={},
            repo_owner="org", repo_name="repo",
        )
        outcome = await validator.validate(result, thread)
        assert outcome.approved is True

    @pytest.mark.asyncio
    async def test_rejects_report_with_no_artifacts(self):
        validator = ReportValidator()
        result = AgentResult(
            branch="", exit_code=0, commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[],
        )
        thread = Thread(
            id="t1", source="slack", source_ref={},
            repo_owner="org", repo_name="repo",
        )
        outcome = await validator.validate(result, thread)
        assert outcome.approved is False
        assert "no artifacts" in outcome.reason.lower()

    @pytest.mark.asyncio
    async def test_approves_report_even_on_nonzero_exit(self):
        validator = ReportValidator()
        result = AgentResult(
            branch="", exit_code=1, commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[
                Artifact(result_type=ResultType.REPORT, location="inline",
                         metadata={"error": "partial results"})
            ],
        )
        thread = Thread(
            id="t1", source="slack", source_ref={},
            repo_owner="org", repo_name="repo",
        )
        outcome = await validator.validate(result, thread)
        assert outcome.approved is True


class TestGetValidator:
    def test_returns_pr_validator_for_pull_request(self):
        v = get_validator(ResultType.PULL_REQUEST)
        assert isinstance(v, PRValidator)

    def test_returns_report_validator_for_report(self):
        v = get_validator(ResultType.REPORT)
        assert isinstance(v, ReportValidator)

    def test_returns_pr_validator_as_fallback(self):
        v = get_validator(ResultType.DB_ROWS)
        assert isinstance(v, PRValidator)


class TestReversibilityMapping:
    def test_pull_request_is_trivial(self):
        assert REVERSIBILITY[ResultType.PULL_REQUEST] == ReversibilityLevel.TRIVIAL

    def test_report_is_trivial(self):
        assert REVERSIBILITY[ResultType.REPORT] == ReversibilityLevel.TRIVIAL

    def test_db_rows_is_possible(self):
        assert REVERSIBILITY[ResultType.DB_ROWS] == ReversibilityLevel.POSSIBLE

    def test_api_response_is_difficult(self):
        assert REVERSIBILITY[ResultType.API_RESPONSE] == ReversibilityLevel.DIFFICULT
