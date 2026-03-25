"""Tests for the workflow compiler."""

from __future__ import annotations

import pytest

from controller.workflows.compiler import CompilationError, WorkflowCompiler
from controller.workflows.models import StepStatus, StepType


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_definition(steps: list[dict]) -> dict:
    """Wrap a list of raw step dicts in a template definition."""
    return {"steps": steps}


SEARCH_AGENT = {
    "task_template": "Search {{ source }} for events in {{ region }}",
    "task_type": "analysis",
    "skills": ["web-search"],
}

SIMPLE_AGENT = {
    "task_template": "Summarize results for {{ query }}",
    "task_type": "analysis",
}


# ---------------------------------------------------------------------------
# 1. test_compile_sequential
# ---------------------------------------------------------------------------


def test_compile_sequential():
    """A single sequential step compiles into one WorkflowStep."""
    definition = _make_definition(
        [
            {
                "id": "summarize",
                "type": "sequential",
                "agent": SIMPLE_AGENT,
            }
        ]
    )
    params = {"query": "AI conferences"}

    compiler = WorkflowCompiler(max_agents_per_execution=20)
    steps = compiler.compile(definition, params, execution_id="exec-1")

    assert len(steps) == 1
    step = steps[0]
    assert step.step_id == "summarize"
    assert step.step_type == StepType.SEQUENTIAL
    assert step.status == StepStatus.PENDING
    assert step.execution_id == "exec-1"
    assert step.input is not None
    assert step.input["task"] == "Summarize results for AI conferences"


# ---------------------------------------------------------------------------
# 2. test_compile_fan_out
# ---------------------------------------------------------------------------


def test_compile_fan_out():
    """Fan-out expands regions x sources into correct agent specs."""
    definition = _make_definition(
        [
            {
                "id": "search",
                "type": "fan_out",
                "over": "regions x sources",
                "agent": SEARCH_AGENT,
                "max_parallel": 10,
                "timeout_seconds": 600,
                "on_failure": "collect_all",
            }
        ]
    )
    params = {
        "regions": ["Dallas", "Austin"],
        "sources": ["eventbrite", "meetup"],
    }

    compiler = WorkflowCompiler(max_agents_per_execution=20)
    steps = compiler.compile(definition, params, execution_id="exec-2")

    assert len(steps) == 1
    step = steps[0]
    assert step.step_type == StepType.FAN_OUT
    agents = step.input["agents"]
    assert len(agents) == 4  # 2 regions x 2 sources

    # Verify interpolation
    tasks = [a["task"] for a in agents]
    assert "Search eventbrite for events in Dallas" in tasks
    assert "Search meetup for events in Austin" in tasks

    # Verify combo params are stored
    combos = [a["params"] for a in agents]
    assert {"region": "Dallas", "source": "eventbrite"} in combos
    assert {"region": "Austin", "source": "meetup"} in combos


# ---------------------------------------------------------------------------
# 3. test_agent_limit_enforced
# ---------------------------------------------------------------------------


def test_agent_limit_enforced():
    """CompilationError raised when fan-out exceeds max_agents."""
    definition = _make_definition(
        [
            {
                "id": "search",
                "type": "fan_out",
                "over": "regions x sources",
                "agent": SEARCH_AGENT,
                "max_parallel": 10,
                "timeout_seconds": 600,
            }
        ]
    )
    params = {
        "regions": ["a", "b", "c", "d", "e"],
        "sources": ["s1", "s2", "s3", "s4", "s5"],
    }

    # 5 x 5 = 25 agents, limit is 20
    compiler = WorkflowCompiler(max_agents_per_execution=20)
    with pytest.raises(CompilationError, match="25 agents.*limit of 20"):
        compiler.compile(definition, params)


# ---------------------------------------------------------------------------
# 4. test_dag_validation_no_cycles
# ---------------------------------------------------------------------------


def test_dag_validation_no_cycles():
    """A valid DAG with dependencies compiles successfully in correct order."""
    definition = _make_definition(
        [
            {
                "id": "search",
                "type": "fan_out",
                "over": "regions",
                "agent": SEARCH_AGENT,
                "max_parallel": 5,
                "timeout_seconds": 600,
            },
            {
                "id": "merge",
                "type": "aggregate",
                "depends_on": ["search"],
                "input": "search.*",
                "strategy": "merge_arrays",
            },
            {
                "id": "dedup",
                "type": "transform",
                "depends_on": ["merge"],
                "input": "merge",
                "operations": [{"op": "deduplicate", "key": "name"}],
            },
        ]
    )
    params = {"regions": ["Dallas", "Austin"], "source": "eventbrite"}

    compiler = WorkflowCompiler(max_agents_per_execution=20)
    steps = compiler.compile(definition, params)

    # Verify topological order
    step_ids = [s.step_id for s in steps]
    assert step_ids.index("search") < step_ids.index("merge")
    assert step_ids.index("merge") < step_ids.index("dedup")


# ---------------------------------------------------------------------------
# 5. test_dag_validation_cycles
# ---------------------------------------------------------------------------


def test_dag_validation_cycles():
    """Cycle in dependency graph raises CompilationError."""
    definition = _make_definition(
        [
            {
                "id": "step_a",
                "type": "sequential",
                "depends_on": ["step_b"],
                "agent": SIMPLE_AGENT,
            },
            {
                "id": "step_b",
                "type": "sequential",
                "depends_on": ["step_a"],
                "agent": SIMPLE_AGENT,
            },
        ]
    )
    params = {"query": "test"}

    compiler = WorkflowCompiler(max_agents_per_execution=20)
    with pytest.raises(CompilationError, match="cycle detected"):
        compiler.compile(definition, params)


# ---------------------------------------------------------------------------
# 6. test_dependency_inference
# ---------------------------------------------------------------------------


def test_dependency_inference():
    """Aggregate with input='search.*' auto-depends on 'search'."""
    definition = _make_definition(
        [
            {
                "id": "search",
                "type": "fan_out",
                "over": "regions",
                "agent": SEARCH_AGENT,
                "max_parallel": 5,
                "timeout_seconds": 600,
            },
            {
                "id": "merge",
                "type": "aggregate",
                # NOTE: no explicit depends_on
                "input": "search.*",
                "strategy": "merge_arrays",
            },
        ]
    )
    params = {"regions": ["Dallas"]}

    compiler = WorkflowCompiler(max_agents_per_execution=20)
    steps = compiler.compile(definition, params)

    step_ids = [s.step_id for s in steps]
    assert step_ids.index("search") < step_ids.index("merge")


# ---------------------------------------------------------------------------
# 7. test_parameter_validation
# ---------------------------------------------------------------------------


def test_parameter_validation_missing_required():
    """Missing required parameter raises CompilationError."""
    definition = _make_definition(
        [
            {
                "id": "search",
                "type": "sequential",
                "agent": SIMPLE_AGENT,
            }
        ]
    )
    schema = {
        "query": {"type": "string", "required": True},
        "regions": {"type": "array", "required": True},
    }
    params = {"query": "test"}  # missing 'regions'

    compiler = WorkflowCompiler(max_agents_per_execution=20)
    with pytest.raises(CompilationError, match="Missing required parameter: regions"):
        compiler.compile(definition, params, parameter_schema=schema)


def test_parameter_validation_type_mismatch():
    """Wrong parameter type raises CompilationError."""
    definition = _make_definition(
        [
            {
                "id": "search",
                "type": "sequential",
                "agent": SIMPLE_AGENT,
            }
        ]
    )
    schema = {
        "query": {"type": "string", "required": True},
    }
    params = {"query": 42}  # should be string

    compiler = WorkflowCompiler(max_agents_per_execution=20)
    with pytest.raises(CompilationError, match="expected type 'string'"):
        compiler.compile(definition, params, parameter_schema=schema)


def test_parameter_validation_default_applied():
    """Default values are applied for missing optional parameters."""
    definition = _make_definition(
        [
            {
                "id": "search",
                "type": "sequential",
                "agent": {
                    "task_template": "Search for {{ query }} limit {{ limit }}",
                    "task_type": "analysis",
                },
            }
        ]
    )
    schema = {
        "query": {"type": "string", "required": True},
        "limit": {"type": "number", "required": False, "default": 10},
    }
    params = {"query": "test"}

    compiler = WorkflowCompiler(max_agents_per_execution=20)
    steps = compiler.compile(definition, params, parameter_schema=schema)

    assert steps[0].input["task"] == "Search for test limit 10"


# ---------------------------------------------------------------------------
# 8. test_safe_interpolation
# ---------------------------------------------------------------------------


def test_safe_interpolation():
    """Task templates are interpolated with safe_interpolate (no code exec)."""
    definition = _make_definition(
        [
            {
                "id": "analyze",
                "type": "sequential",
                "agent": {
                    "task_template": "Analyze {{ topic }} in {{ city }} for {{ year }}",
                    "task_type": "analysis",
                },
            }
        ]
    )
    params = {"topic": "AI", "city": "Austin", "year": "2026"}

    compiler = WorkflowCompiler(max_agents_per_execution=20)
    steps = compiler.compile(definition, params)

    assert steps[0].input["task"] == "Analyze AI in Austin for 2026"


def test_safe_interpolation_unknown_keys_preserved():
    """Unknown {{ keys }} are left as-is (not stripped or errored)."""
    definition = _make_definition(
        [
            {
                "id": "report",
                "type": "sequential",
                "agent": {
                    "task_template": "Report on {{ topic }} with {{ unknown_var }}",
                    "task_type": "analysis",
                },
            }
        ]
    )
    params = {"topic": "AI"}

    compiler = WorkflowCompiler(max_agents_per_execution=20)
    steps = compiler.compile(definition, params)

    assert steps[0].input["task"] == "Report on AI with {{ unknown_var }}"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_empty_steps_raises():
    """Template with no steps raises CompilationError."""
    compiler = WorkflowCompiler()
    with pytest.raises(CompilationError, match="no steps"):
        compiler.compile({"steps": []}, {})


def test_duplicate_step_id_raises():
    """Duplicate step IDs raise CompilationError."""
    definition = _make_definition(
        [
            {"id": "search", "type": "sequential", "agent": SIMPLE_AGENT},
            {"id": "search", "type": "sequential", "agent": SIMPLE_AGENT},
        ]
    )
    compiler = WorkflowCompiler()
    with pytest.raises(CompilationError, match="Duplicate step id"):
        compiler.compile(definition, {"query": "test"})


def test_unknown_dependency_raises():
    """Depending on a non-existent step raises CompilationError."""
    definition = _make_definition(
        [
            {
                "id": "merge",
                "type": "aggregate",
                "depends_on": ["nonexistent"],
                "input": "nonexistent.*",
                "strategy": "merge_arrays",
            }
        ]
    )
    compiler = WorkflowCompiler()
    with pytest.raises(CompilationError, match="unknown step 'nonexistent'"):
        compiler.compile(definition, {})
