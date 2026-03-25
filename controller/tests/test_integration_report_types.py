"""Tests for integration result reporting by result type."""
from controller.models import (
    AgentResult, ResultType, Artifact,
)
from controller.integrations.formatting import format_result_message


class TestResultMessageFormatting:
    def test_pr_result_message_mentions_pr(self):
        result = AgentResult(
            branch="df/abc/123", exit_code=0, commit_count=2,
            pr_url="https://github.com/org/repo/pull/1",
        )
        msg = format_result_message(result)
        assert "pull request" in msg.lower() or "pr" in msg.lower()

    def test_report_result_message_mentions_findings(self):
        result = AgentResult(
            branch="", exit_code=0, commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[
                Artifact(result_type=ResultType.REPORT, location="inline",
                         metadata={"summary": "Found 3 issues"})
            ],
        )
        msg = format_result_message(result)
        assert "report" in msg.lower() or "analysis" in msg.lower()
        assert "Found 3 issues" in msg

    def test_failed_result_message(self):
        result = AgentResult(
            branch="df/abc/123", exit_code=1, commit_count=0,
            stderr="ImportError: no module named foo",
        )
        msg = format_result_message(result)
        assert "failed" in msg.lower() or "error" in msg.lower()

    def test_pr_with_commits_no_url(self):
        result = AgentResult(
            branch="df/abc/123", exit_code=0, commit_count=3,
        )
        msg = format_result_message(result)
        assert "3" in msg
        assert "commit" in msg.lower()

    def test_pr_no_changes(self):
        result = AgentResult(
            branch="df/abc/123", exit_code=0, commit_count=0,
        )
        msg = format_result_message(result)
        assert "no changes" in msg.lower()

    def test_report_with_file_artifact(self):
        result = AgentResult(
            branch="", exit_code=0, commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[
                Artifact(result_type=ResultType.FILE_ARTIFACT,
                         location="s3://bucket/output.csv",
                         metadata={"summary": "Export complete"})
            ],
        )
        msg = format_result_message(result)
        assert "s3://bucket/output.csv" in msg
