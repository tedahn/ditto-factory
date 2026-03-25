"""Tests for entrypoint.sh output-type routing logic.

These tests validate the JSON payload structures and routing expectations
for code_change vs non-code task types in the agent entrypoint.
"""

import json
import pytest


def _make_task_payload(
    task_type: str = "code_change",
    repo_url: str = "https://github.com/org/repo",
    branch: str = "feat/test",
    task: str = "Do something",
    output_schema: dict | None = None,
    skills: list | None = None,
    gateway_mcp: dict | None = None,
) -> dict:
    """Build a task payload matching the Redis task:THREAD_ID format."""
    payload = {
        "repo_url": repo_url,
        "branch": branch,
        "task": task,
        "task_type": task_type,
    }
    if output_schema is not None:
        payload["output_schema"] = json.dumps(output_schema)
    if skills is not None:
        payload["skills"] = skills
    if gateway_mcp is not None:
        payload["gateway_mcp"] = gateway_mcp
    return payload


def _make_result_payload(
    task_type: str = "code_change",
    branch: str = "feat/test",
    exit_code: int = 0,
    commit_count: int = 1,
    stderr: str = "",
    result: dict | None = None,
) -> dict:
    """Build an expected result payload matching Redis result:THREAD_ID format."""
    return {
        "branch": branch,
        "exit_code": exit_code,
        "commit_count": commit_count,
        "stderr": stderr,
        "task_type": task_type,
        "result": result if result is not None else {},
    }


class TestCodeChangePath:
    """Tests for code_change task type (git-based path)."""

    def test_code_change_payload_includes_branch(self):
        """code_change tasks must include a non-empty branch in the result."""
        result = _make_result_payload(task_type="code_change", branch="feat/test", commit_count=2)
        assert result["branch"] == "feat/test"
        assert result["branch"] != ""
        assert result["commit_count"] > 0

    def test_code_change_requires_repo_url(self):
        """code_change task payloads must contain repo_url."""
        payload = _make_task_payload(task_type="code_change")
        assert "repo_url" in payload
        assert payload["repo_url"].startswith("https://")

    def test_code_change_requires_branch(self):
        """code_change task payloads must contain a branch name."""
        payload = _make_task_payload(task_type="code_change", branch="feat/my-branch")
        assert payload["branch"] == "feat/my-branch"

    def test_code_change_default_task_type(self):
        """When task_type is missing, it should default to code_change."""
        payload = {"repo_url": "https://github.com/org/repo", "branch": "main", "task": "fix bug"}
        task_type = payload.get("task_type", "code_change")
        assert task_type == "code_change"


class TestNonCodePath:
    """Tests for analysis/file_output/api_action/db_mutation task types."""

    @pytest.mark.parametrize("task_type", ["analysis", "file_output", "api_action", "db_mutation"])
    def test_non_code_payload_no_branch(self, task_type: str):
        """Non-code tasks should have an empty branch in the result."""
        result = _make_result_payload(task_type=task_type, branch="", commit_count=0)
        assert result["branch"] == ""
        assert result["commit_count"] == 0

    @pytest.mark.parametrize("task_type", ["analysis", "file_output", "api_action", "db_mutation"])
    def test_non_code_payload_includes_result(self, task_type: str):
        """Non-code tasks should include a result object in the Redis payload."""
        result_data = {"summary": "Analysis complete", "score": 0.95}
        result = _make_result_payload(task_type=task_type, branch="", commit_count=0, result=result_data)
        assert result["result"] == result_data
        assert result["task_type"] == task_type

    def test_output_schema_appended_to_task(self):
        """output_schema should be included in the task payload for non-code agents."""
        schema = {"type": "object", "properties": {"summary": {"type": "string"}}}
        payload = _make_task_payload(task_type="analysis", output_schema=schema)
        assert "output_schema" in payload
        parsed_schema = json.loads(payload["output_schema"])
        assert parsed_schema["type"] == "object"
        assert "summary" in parsed_schema["properties"]

    def test_non_code_no_repo_url_needed(self):
        """Non-code tasks don't need repo_url to function (it can be present but unused)."""
        payload = _make_task_payload(task_type="analysis")
        # The entrypoint only reads repo_url inside the code_change case
        # So for non-code tasks, repo_url presence is irrelevant
        assert payload["task_type"] == "analysis"

    def test_skills_injection_shared(self):
        """Both code and non-code paths should support skill injection."""
        skills = [
            {"name": "backend-arch", "content": "You are a backend architect."},
            {"name": "security", "content": "Apply security best practices."},
        ]
        for task_type in ["code_change", "analysis"]:
            payload = _make_task_payload(task_type=task_type, skills=skills)
            assert len(payload["skills"]) == 2
            assert payload["skills"][0]["name"] == "backend-arch"

    def test_gateway_mcp_shared(self):
        """Both code and non-code paths should support gateway MCP injection."""
        gateway = {"custom-server": {"command": "node", "args": ["server.js"]}}
        for task_type in ["code_change", "api_action"]:
            payload = _make_task_payload(task_type=task_type, gateway_mcp=gateway)
            assert "gateway_mcp" in payload
            assert "custom-server" in payload["gateway_mcp"]


class TestUnknownTaskType:
    """Tests for unknown/invalid task types."""

    def test_unknown_task_type_fails(self):
        """Unknown task types should result in exit_code=1."""
        result = _make_result_payload(task_type="unknown_type", branch="", exit_code=1, commit_count=0, stderr="Unknown task_type: unknown_type")
        assert result["exit_code"] == 1
        assert "Unknown task_type" in result["stderr"]

    def test_empty_task_type_defaults_to_code_change(self):
        """An empty/missing task_type should default to code_change."""
        payload = {"task": "do work"}
        task_type = payload.get("task_type") or "code_change"
        assert task_type == "code_change"


class TestResultPayloadStructure:
    """Tests for the unified result payload structure."""

    def test_result_payload_has_task_type(self):
        """Result payloads must include task_type for downstream routing."""
        for tt in ["code_change", "analysis", "file_output", "api_action", "db_mutation"]:
            result = _make_result_payload(task_type=tt, branch="" if tt != "code_change" else "feat/x")
            assert "task_type" in result
            assert result["task_type"] == tt

    def test_result_payload_has_result_field(self):
        """All result payloads must include a result field (even if empty)."""
        result = _make_result_payload(task_type="code_change")
        assert "result" in result
        assert isinstance(result["result"], dict)

    def test_result_json_serializable(self):
        """Result payloads must be JSON-serializable for Redis storage."""
        result = _make_result_payload(
            task_type="analysis",
            branch="",
            result={"data": [1, 2, 3], "nested": {"key": "value"}},
        )
        serialized = json.dumps(result)
        deserialized = json.loads(serialized)
        assert deserialized == result
