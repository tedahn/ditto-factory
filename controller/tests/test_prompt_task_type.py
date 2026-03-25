"""Tests for task-type-aware prompt building."""
from controller.prompt.builder import build_system_prompt
from controller.models import TaskType


class TestPromptTaskType:
    def test_code_change_prompt_has_commit_instructions(self):
        prompt = build_system_prompt(
            repo_owner="org", repo_name="repo",
            task="fix the bug",
            task_type=TaskType.CODE_CHANGE,
        )
        assert "commit" in prompt.lower()
        assert "push your branch" in prompt.lower()

    def test_analysis_prompt_has_no_commit_instructions(self):
        prompt = build_system_prompt(
            repo_owner="org", repo_name="repo",
            task="analyze error rates",
            task_type=TaskType.ANALYSIS,
        )
        assert "push your branch" not in prompt.lower()
        assert "report" in prompt.lower() or "findings" in prompt.lower()

    def test_default_task_type_is_code_change(self):
        prompt = build_system_prompt(
            repo_owner="org", repo_name="repo",
            task="fix the bug",
        )
        assert "commit" in prompt.lower()

    def test_analysis_prompt_mentions_artifacts(self):
        prompt = build_system_prompt(
            repo_owner="org", repo_name="repo",
            task="audit the codebase",
            task_type=TaskType.ANALYSIS,
        )
        assert "artifact" in prompt.lower() or "result" in prompt.lower()
