"""Tests for WorkflowCompiler — template compilation and DAG validation.

Pure unit tests — no database required.
"""

from __future__ import annotations

import pytest

from controller.workflows.compiler import CompilationError, WorkflowCompiler
from controller.workflows.models import StepType


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def compiler():
    return WorkflowCompiler(max_agents_per_execution=10)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sequential_steps(compiler):
    """Compile a simple two-step sequential workflow."""
    definition = {
        "steps": [
            {
                "id": "step1",
                "type": "sequential",
                "agent": {"task_template": "Do {{ task }}", "task_type": "analysis"},
            },
            {
                "id": "step2",
                "type": "sequential",
                "depends_on": ["step1"],
                "agent": {"task_template": "Review results", "task_type": "review"},
            },
        ]
    }
    params = {"task": "analysis"}
    steps = compiler.compile(definition, params, execution_id="exec-1")

    assert len(steps) == 2
    assert steps[0].step_type == StepType.SEQUENTIAL
    assert steps[0].step_id == "step1"
    assert steps[0].input["task"] == "Do analysis"
    assert steps[1].step_id == "step2"
    assert "step1" in steps[1].input["depends_on"]


@pytest.mark.asyncio
async def test_fan_out_expansion(compiler):
    """Fan-out step expands over parameter arrays."""
    definition = {
        "steps": [
            {
                "id": "search",
                "type": "fan_out",
                "agent": {
                    "task_template": "Search {{ region }} for {{ topic }}",
                    "task_type": "analysis",
                },
                "fan_out": {"over": "regions", "max_parallel": 5},
            }
        ]
    }
    params = {"regions": ["US", "EU", "APAC"], "topic": "events"}
    steps = compiler.compile(definition, params, execution_id="exec-2")

    assert len(steps) == 1
    step = steps[0]
    assert step.step_type == StepType.FAN_OUT
    agents = step.input["agents"]
    assert len(agents) == 3
    assert agents[0]["task"] == "Search US for events"
    assert agents[1]["task"] == "Search EU for events"
    assert agents[2]["task"] == "Search APAC for events"


@pytest.mark.asyncio
async def test_agent_limit_enforcement():
    """Compiler should reject workflows that exceed agent limit."""
    compiler = WorkflowCompiler(max_agents_per_execution=2)
    definition = {
        "steps": [
            {
                "id": "search",
                "type": "fan_out",
                "agent": {"task_template": "Search {{ region }}"},
                "fan_out": {"over": "regions"},
            }
        ]
    }
    params = {"regions": ["US", "EU", "APAC"]}  # 3 agents > limit of 2

    with pytest.raises(CompilationError, match="limit"):
        compiler.compile(definition, params)


@pytest.mark.asyncio
async def test_dag_cycle_detection(compiler):
    """Compiler should reject workflows with dependency cycles."""
    definition = {
        "steps": [
            {"id": "a", "type": "sequential", "depends_on": ["c"],
             "agent": {"task_template": "A"}},
            {"id": "b", "type": "sequential", "depends_on": ["a"],
             "agent": {"task_template": "B"}},
            {"id": "c", "type": "sequential", "depends_on": ["b"],
             "agent": {"task_template": "C"}},
        ]
    }
    with pytest.raises(CompilationError, match="cycle"):
        compiler.compile(definition, {})


@pytest.mark.asyncio
async def test_dependency_inference(compiler):
    """Aggregate step should auto-depend on its input step."""
    definition = {
        "steps": [
            {
                "id": "search",
                "type": "fan_out",
                "agent": {"task_template": "Search"},
                "fan_out": {"over": "regions"},
            },
            {
                "id": "merge",
                "type": "aggregate",
                # No explicit depends_on — should be inferred from input
                "aggregate": {"input": "search.*", "strategy": "merge_arrays"},
            },
        ]
    }
    params = {"regions": ["US"]}
    steps = compiler.compile(definition, params, execution_id="exec-5")

    assert len(steps) == 2
    merge_step = steps[1]
    assert merge_step.step_id == "merge"
    assert "search" in merge_step.input["depends_on"]


@pytest.mark.asyncio
async def test_parameter_validation(compiler):
    """Compiler should reject missing required parameters."""
    definition = {
        "steps": [
            {
                "id": "step1",
                "type": "sequential",
                "agent": {"task_template": "Do work"},
            }
        ]
    }
    schema = {
        "type": "object",
        "required": ["regions", "topic"],
        "properties": {
            "regions": {"type": "array"},
            "topic": {"type": "string"},
        },
    }

    with pytest.raises(CompilationError, match="Missing required parameter"):
        compiler.compile(definition, {"regions": ["US"]}, parameter_schema=schema)

    # Type validation
    with pytest.raises(CompilationError, match="must be of type"):
        compiler.compile(
            definition,
            {"regions": "not-a-list", "topic": "test"},
            parameter_schema=schema,
        )


@pytest.mark.asyncio
async def test_safe_interpolation(compiler):
    """safe_interpolate should handle missing keys gracefully."""
    definition = {
        "steps": [
            {
                "id": "step1",
                "type": "sequential",
                "agent": {
                    "task_template": "Hello {{ name }}, unknown {{ missing }}",
                },
            }
        ]
    }
    params = {"name": "World"}
    steps = compiler.compile(definition, params, execution_id="exec-7")

    assert len(steps) == 1
    task = steps[0].input["task"]
    assert "Hello World" in task
    assert "{{ missing }}" in task  # unknown keys left as-is


@pytest.mark.asyncio
async def test_empty_steps(compiler):
    """Compiler should return empty list for empty step definitions."""
    result = compiler.compile({"steps": []}, {})
    assert result == []

    result = compiler.compile({}, {})
    assert result == []
