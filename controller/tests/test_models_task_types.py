"""Tests for TaskType, ResultType enums and Artifact dataclass."""
from controller.models import (
    TaskType,
    ResultType,
    ReversibilityLevel,
    Artifact,
    TaskRequest,
    AgentResult,
)


class TestTaskTypeEnum:
    def test_code_change_is_default(self):
        assert TaskType.CODE_CHANGE == "code_change"

    def test_all_task_types_exist(self):
        assert TaskType.CODE_CHANGE == "code_change"
        assert TaskType.ANALYSIS == "analysis"
        assert TaskType.DB_MUTATION == "db_mutation"
        assert TaskType.FILE_OUTPUT == "file_output"
        assert TaskType.API_ACTION == "api_action"

    def test_task_type_is_str_enum(self):
        assert isinstance(TaskType.CODE_CHANGE, str)
        assert TaskType.CODE_CHANGE == "code_change"


class TestResultTypeEnum:
    def test_pull_request_exists(self):
        assert ResultType.PULL_REQUEST == "pull_request"

    def test_all_result_types_exist(self):
        assert ResultType.PULL_REQUEST == "pull_request"
        assert ResultType.REPORT == "report"
        assert ResultType.DB_ROWS == "db_rows"
        assert ResultType.FILE_ARTIFACT == "file_artifact"
        assert ResultType.API_RESPONSE == "api_response"


class TestReversibilityLevel:
    def test_all_levels_exist(self):
        assert ReversibilityLevel.TRIVIAL == "trivial"
        assert ReversibilityLevel.POSSIBLE == "possible"
        assert ReversibilityLevel.DIFFICULT == "difficult"
        assert ReversibilityLevel.IMPOSSIBLE == "impossible"


class TestArtifact:
    def test_artifact_creation(self):
        a = Artifact(
            result_type=ResultType.REPORT,
            location="s3://bucket/report.json",
            metadata={"rows": 100},
        )
        assert a.result_type == ResultType.REPORT
        assert a.location == "s3://bucket/report.json"
        assert a.metadata == {"rows": 100}

    def test_artifact_defaults(self):
        a = Artifact(
            result_type=ResultType.FILE_ARTIFACT,
            location="/tmp/output.csv",
        )
        assert a.metadata == {}
        assert a.id is not None


class TestTaskRequestBackwardsCompat:
    def test_task_request_defaults_to_code_change(self):
        tr = TaskRequest(
            thread_id="t1",
            source="slack",
            source_ref={},
            repo_owner="org",
            repo_name="repo",
            task="fix the bug",
        )
        assert tr.task_type == TaskType.CODE_CHANGE

    def test_task_request_accepts_explicit_task_type(self):
        tr = TaskRequest(
            thread_id="t1",
            source="slack",
            source_ref={},
            repo_owner="org",
            repo_name="repo",
            task="analyze logs",
            task_type=TaskType.ANALYSIS,
        )
        assert tr.task_type == TaskType.ANALYSIS


class TestAgentResultBackwardsCompat:
    def test_agent_result_defaults_to_pull_request(self):
        ar = AgentResult(branch="main", exit_code=0, commit_count=1)
        assert ar.result_type == ResultType.PULL_REQUEST
        assert ar.artifacts == []

    def test_agent_result_accepts_report_type(self):
        ar = AgentResult(
            branch="",
            exit_code=0,
            commit_count=0,
            result_type=ResultType.REPORT,
            artifacts=[
                Artifact(
                    result_type=ResultType.REPORT,
                    location="inline",
                    metadata={"summary": "all good"},
                )
            ],
        )
        assert ar.result_type == ResultType.REPORT
        assert len(ar.artifacts) == 1
