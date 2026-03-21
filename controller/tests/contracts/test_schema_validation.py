"""Schema Validation Tests.

Validates JSON Schema definitions for cross-process contracts
(TaskContext and AgentResult).
"""
import json
from pathlib import Path

import pytest

try:
    import jsonschema

    HAS_JSONSCHEMA = True
except ImportError:
    HAS_JSONSCHEMA = False

SCHEMA_DIR = Path(__file__).parent.parent / "schemas"

pytestmark = pytest.mark.skipif(not HAS_JSONSCHEMA, reason="jsonschema not installed")


@pytest.fixture
def task_context_schema():
    return json.loads((SCHEMA_DIR / "task_context.schema.json").read_text())


@pytest.fixture
def agent_result_schema():
    return json.loads((SCHEMA_DIR / "agent_result.schema.json").read_text())


class TestTaskContextSchema:
    def test_valid_context_passes(self, task_context_schema):
        context = {
            "task": "fix the bug",
            "system_prompt": "You are a coding agent",
            "repo_url": "https://github.com/org/repo.git",
            "branch": "df/github/abc123",
        }
        jsonschema.validate(context, task_context_schema)

    def test_missing_task_fails(self, task_context_schema):
        context = {"system_prompt": "x", "repo_url": "https://github.com/o/r.git", "branch": "df/x"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(context, task_context_schema)

    def test_missing_system_prompt_fails(self, task_context_schema):
        context = {"task": "t", "repo_url": "https://github.com/o/r.git", "branch": "df/x"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(context, task_context_schema)

    def test_missing_repo_url_fails(self, task_context_schema):
        context = {"task": "t", "system_prompt": "s", "branch": "df/x"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(context, task_context_schema)

    def test_missing_branch_fails(self, task_context_schema):
        context = {"task": "t", "system_prompt": "s", "repo_url": "https://github.com/o/r.git"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(context, task_context_schema)

    def test_invalid_repo_url_fails(self, task_context_schema):
        context = {
            "task": "t",
            "system_prompt": "s",
            "repo_url": "git@github.com:org/repo.git",  # SSH, not HTTPS
            "branch": "df/x",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(context, task_context_schema)

    def test_branch_not_starting_with_df_fails(self, task_context_schema):
        context = {
            "task": "t",
            "system_prompt": "s",
            "repo_url": "https://github.com/o/r.git",
            "branch": "feature/x",  # Does not start with df/
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(context, task_context_schema)

    def test_extra_fields_allowed(self, task_context_schema):
        context = {
            "task": "t",
            "system_prompt": "s",
            "repo_url": "https://github.com/o/r.git",
            "branch": "df/x",
            "extra": "allowed",
        }
        jsonschema.validate(context, task_context_schema)  # Should not raise


class TestAgentResultSchema:
    def test_valid_result_passes(self, agent_result_schema):
        result = {"branch": "df/test/x", "exit_code": 0, "commit_count": 3, "stderr": ""}
        jsonschema.validate(result, agent_result_schema)

    def test_string_numbers_pass(self, agent_result_schema):
        result = {"branch": "df/test/x", "exit_code": "0", "commit_count": "3", "stderr": ""}
        jsonschema.validate(result, agent_result_schema)

    def test_missing_branch_fails(self, agent_result_schema):
        result = {"exit_code": 0, "commit_count": 0}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(result, agent_result_schema)

    def test_missing_exit_code_fails(self, agent_result_schema):
        result = {"branch": "df/test/x", "commit_count": 0}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(result, agent_result_schema)

    def test_missing_commit_count_fails(self, agent_result_schema):
        result = {"branch": "df/test/x", "exit_code": 0}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(result, agent_result_schema)

    def test_extra_fields_allowed(self, agent_result_schema):
        result = {
            "branch": "df/test/x",
            "exit_code": 0,
            "commit_count": 0,
            "stderr": "",
            "tokens_used": 5000,
        }
        jsonschema.validate(result, agent_result_schema)

    def test_null_pr_url_allowed(self, agent_result_schema):
        result = {
            "branch": "df/test/x",
            "exit_code": 0,
            "commit_count": 1,
            "pr_url": None,
        }
        jsonschema.validate(result, agent_result_schema)

    def test_string_pr_url_allowed(self, agent_result_schema):
        result = {
            "branch": "df/test/x",
            "exit_code": 0,
            "commit_count": 1,
            "pr_url": "https://github.com/org/repo/pull/42",
        }
        jsonschema.validate(result, agent_result_schema)
