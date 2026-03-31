"""Tests for the codebase-analysis workflow template.

Verifies template compilation, step ordering, parameter validation,
and quality metadata schema.
"""

from __future__ import annotations

import pytest

from controller.workflows.compiler import CompilationError, WorkflowCompiler
from controller.workflows.models import StepType
from controller.workflows.templates.codebase_analysis import (
    DEFINITION,
    PARAMETER_SCHEMA,
    SLUG,
    get_definition,
)


@pytest.fixture
def compiler():
    return WorkflowCompiler(max_agents_per_execution=20)


@pytest.fixture
def valid_params():
    return {
        "repo_owner": "acme",
        "repo_name": "my-service",
        "branch": "main",
        "output_dir": "/workspace/output",
    }


class TestDefinitionStructure:
    """Verify the template definition is well-formed."""

    def test_definition_has_four_steps(self):
        defn = get_definition()
        assert len(defn["steps"]) == 4

    def test_step_ids(self):
        defn = get_definition()
        step_ids = [s["id"] for s in defn["steps"]]
        assert step_ids == [
            "domain-expert",
            "standards-discoverer",
            "work-item-planner",
            "synthesis-report",
        ]

    def test_step_types(self):
        defn = get_definition()
        types = [s["type"] for s in defn["steps"]]
        assert types == ["sequential", "sequential", "sequential", "sequential"]

    def test_dependency_chain(self):
        defn = get_definition()
        steps = {s["id"]: s for s in defn["steps"]}
        # domain-expert has no dependencies
        assert steps["domain-expert"].get("depends_on", []) == []
        # standards-discoverer depends on domain-expert
        assert steps["standards-discoverer"]["depends_on"] == ["domain-expert"]
        # work-item-planner depends on both
        assert set(steps["work-item-planner"]["depends_on"]) == {
            "domain-expert",
            "standards-discoverer",
        }
        # synthesis-report depends on all three prior steps
        assert set(steps["synthesis-report"]["depends_on"]) == {
            "domain-expert",
            "standards-discoverer",
            "work-item-planner",
        }

    def test_all_steps_have_agent_spec(self):
        defn = get_definition()
        for step in defn["steps"]:
            assert "agent" in step, f"Step {step['id']} missing agent spec"
            assert "task_template" in step["agent"]
            assert step["agent"]["task_type"] == "analysis"

    def test_slug_is_correct(self):
        assert SLUG == "codebase-analysis"


class TestCompilation:
    """Verify the template compiles correctly with the WorkflowCompiler."""

    def test_compiles_to_four_steps(self, compiler, valid_params):
        steps = compiler.compile(
            DEFINITION, valid_params, PARAMETER_SCHEMA
        )
        assert len(steps) == 4

    def test_compiled_step_types(self, compiler, valid_params):
        steps = compiler.compile(
            DEFINITION, valid_params, PARAMETER_SCHEMA
        )
        types = [s.step_type for s in steps]
        assert types == [
            StepType.SEQUENTIAL,
            StepType.SEQUENTIAL,
            StepType.SEQUENTIAL,
            StepType.SEQUENTIAL,
        ]

    def test_compiled_step_ids_match(self, compiler, valid_params):
        steps = compiler.compile(
            DEFINITION, valid_params, PARAMETER_SCHEMA
        )
        step_ids = [s.step_id for s in steps]
        assert step_ids == [
            "domain-expert",
            "standards-discoverer",
            "work-item-planner",
            "synthesis-report",
        ]

    def test_parameters_interpolated_in_task(self, compiler, valid_params):
        steps = compiler.compile(
            DEFINITION, valid_params, PARAMETER_SCHEMA
        )
        # The domain-expert step task should have the repo info interpolated
        task = steps[0].input["task"]
        assert "acme" in task
        assert "my-service" in task
        assert "main" in task
        assert "/workspace/output" in task

    def test_agent_count_within_limit(self, compiler, valid_params):
        # 3 sequential + 1 report = 4 agents, well under 20 limit
        steps = compiler.compile(
            DEFINITION, valid_params, PARAMETER_SCHEMA
        )
        assert len(steps) <= 20


class TestParameterValidation:
    """Verify parameter schema enforcement."""

    def test_missing_required_repo_owner(self, compiler):
        params = {"repo_name": "x", "output_dir": "/out"}
        with pytest.raises(CompilationError, match="repo_owner"):
            compiler.compile(DEFINITION, params, PARAMETER_SCHEMA)

    def test_missing_required_repo_name(self, compiler):
        params = {"repo_owner": "x", "output_dir": "/out"}
        with pytest.raises(CompilationError, match="repo_name"):
            compiler.compile(DEFINITION, params, PARAMETER_SCHEMA)

    def test_missing_required_output_dir(self, compiler):
        params = {"repo_owner": "x", "repo_name": "y", "branch": "main"}
        with pytest.raises(CompilationError, match="output_dir"):
            compiler.compile(DEFINITION, params, PARAMETER_SCHEMA)

    def test_missing_required_branch(self, compiler):
        params = {"repo_owner": "x", "repo_name": "y", "output_dir": "/out"}
        with pytest.raises(CompilationError, match="branch"):
            compiler.compile(DEFINITION, params, PARAMETER_SCHEMA)

    def test_wrong_type_repo_owner(self, compiler):
        params = {"repo_owner": 123, "repo_name": "x", "branch": "main", "output_dir": "/out"}
        with pytest.raises(CompilationError, match="repo_owner"):
            compiler.compile(DEFINITION, params, PARAMETER_SCHEMA)


class TestPromptContent:
    """Verify prompt templates contain required sections."""

    def test_domain_expert_prompt_has_sections(self):
        from controller.workflows.prompts.domain_expert import PROMPT
        assert "## Setup" in PROMPT
        assert "## Tasks" in PROMPT
        assert "## Quality Gate" in PROMPT
        assert "Bounded Contexts" in PROMPT
        assert "Dependency Graph" in PROMPT
        assert "Risk Areas" in PROMPT

    def test_standards_discoverer_prompt_has_sections(self):
        from controller.workflows.prompts.standards_discoverer import PROMPT
        assert "## Setup" in PROMPT
        assert "Naming Conventions" in PROMPT
        assert "Architecture Patterns" in PROMPT
        assert "Anti-Patterns" in PROMPT
        assert "Consistency Scores" in PROMPT
        assert "domain-map.md" in PROMPT

    def test_work_item_planner_prompt_has_sections(self):
        from controller.workflows.prompts.work_item_planner import PROMPT
        assert "## Setup" in PROMPT
        assert "CA-E01" in PROMPT
        assert "blocks" in PROMPT
        assert "blocked_by" in PROMPT
        assert "Ready List" in PROMPT
        assert "domain-map.md" in PROMPT
        assert "standards-index.md" in PROMPT

    def test_synthesis_report_prompt_has_sections(self):
        from controller.workflows.prompts.synthesis_report import PROMPT
        assert "Executive Summary" in PROMPT
        assert "Risk Matrix" in PROMPT
        assert "Recommended Next Actions" in PROMPT
        assert "Quality Gate" in PROMPT
        assert "domain-map.md" in PROMPT
        assert "standards-index.md" in PROMPT
        assert "work-items-backlog.md" in PROMPT

    def test_all_prompts_have_template_variables(self):
        from controller.workflows.prompts.domain_expert import PROMPT as p1
        from controller.workflows.prompts.standards_discoverer import PROMPT as p2
        from controller.workflows.prompts.work_item_planner import PROMPT as p3
        from controller.workflows.prompts.synthesis_report import PROMPT as p4

        for prompt in [p1, p2, p3]:
            assert "{{ repo_owner }}" in prompt
            assert "{{ repo_name }}" in prompt
            assert "{{ branch }}" in prompt
            assert "{{ output_dir }}" in prompt

        # Synthesis report uses repo_owner/repo_name/branch in output template
        # and output_dir for file paths
        assert "{{ output_dir }}" in p4
        assert "{{ repo_owner }}" in p4
