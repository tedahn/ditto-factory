"""Workflow compiler: validates template + params, expands fan-out, builds execution plan."""

from __future__ import annotations

import json
import logging
import re
import uuid
from collections import defaultdict, deque
from dataclasses import replace

from controller.workflows.models import (
    AgentSpec,
    AggregateConfig,
    FanOutConfig,
    StepDefinition,
    StepStatus,
    StepType,
    TransformConfig,
    TransformOp,
    WorkflowStep,
    expand_fan_out,
    safe_interpolate,
)

logger = logging.getLogger(__name__)

# Type mapping for parameter validation (no jsonschema dependency)
_TYPE_MAP: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "number": (int, float),
    "boolean": bool,
    "array": list,
    "object": dict,
}


class CompilationError(Exception):
    """Raised when a template cannot be compiled."""


class WorkflowCompiler:
    """Compiles a workflow template definition + parameters into executable steps.

    The compiler is deterministic -- no LLM calls, no code evaluation.
    All template interpolation uses :func:`safe_interpolate` (simple ``{{ key }}``
    replacement).  Fan-out expansion uses :func:`expand_fan_out`.
    """

    def __init__(self, max_agents_per_execution: int = 20) -> None:
        self._max_agents = max_agents_per_execution

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def compile(
        self,
        template_definition: dict,
        parameters: dict,
        parameter_schema: dict | None = None,
        execution_id: str = "",
    ) -> list[WorkflowStep]:
        """Compile a template definition + parameters into executable steps.

        Pipeline:
        1. Validate parameters against schema
        2. Parse step definitions
        3. Infer implicit dependencies from ``input`` references
        4. Validate DAG (topological sort -- no cycles)
        5. Enforce agent limits
        6. Expand each step into a :class:`WorkflowStep`
        7. Return topologically-ordered list of ``WorkflowStep``
        """
        if not execution_id:
            execution_id = uuid.uuid4().hex

        # 1. Validate parameters
        self._validate_parameters(parameters, parameter_schema)

        # 2. Parse steps
        steps = self._parse_steps(template_definition)

        # 3. Infer dependencies
        steps = self._infer_dependencies(steps)

        # 4. Topological sort (validates DAG)
        steps = self._validate_dag(steps)

        # 5. Enforce agent limit
        agent_count = self._count_agents(steps, parameters)
        if agent_count > self._max_agents:
            raise CompilationError(
                f"Workflow would spawn {agent_count} agents, "
                f"exceeding the limit of {self._max_agents}"
            )

        # 6. Expand each step into a WorkflowStep
        workflow_steps: list[WorkflowStep] = []
        for step_def in steps:
            ws = self._expand_step(step_def, parameters, execution_id)
            workflow_steps.append(ws)

        logger.info(
            "Compiled %d steps (%d agents) for execution %s",
            len(workflow_steps),
            agent_count,
            execution_id,
        )
        return workflow_steps

    # ------------------------------------------------------------------
    # Parameter validation
    # ------------------------------------------------------------------

    def _validate_parameters(self, params: dict, schema: dict | None) -> None:
        """Basic type checking against *parameter_schema*.

        Checks:
        - All required fields are present.
        - Values match declared types (string, number, boolean, array, object).
        - Applies defaults for missing optional parameters.

        No ``jsonschema`` dependency -- intentionally simple.
        """
        if schema is None:
            return

        for name, spec in schema.items():
            required = spec.get("required", True)
            has_default = "default" in spec

            if name not in params:
                if has_default:
                    params[name] = spec["default"]
                elif required:
                    raise CompilationError(
                        f"Missing required parameter: {name}"
                    )
                else:
                    continue

            # Type check
            expected_type = spec.get("type")
            if expected_type and name in params:
                py_type = _TYPE_MAP.get(expected_type)
                if py_type and not isinstance(params[name], py_type):
                    raise CompilationError(
                        f"Parameter '{name}' expected type '{expected_type}', "
                        f"got '{type(params[name]).__name__}'"
                    )

    # ------------------------------------------------------------------
    # Step parsing
    # ------------------------------------------------------------------

    def _parse_steps(self, definition: dict) -> list[StepDefinition]:
        """Parse raw step dicts from the template definition into
        :class:`StepDefinition` dataclasses.
        """
        raw_steps = definition.get("steps", [])
        if not raw_steps:
            raise CompilationError("Template definition contains no steps")

        parsed: list[StepDefinition] = []
        seen_ids: set[str] = set()

        for raw in raw_steps:
            step_id = raw.get("id")
            if not step_id:
                raise CompilationError("Step missing required 'id' field")
            if step_id in seen_ids:
                raise CompilationError(f"Duplicate step id: '{step_id}'")
            seen_ids.add(step_id)

            step_type = StepType(raw["type"])
            depends_on = list(raw.get("depends_on", []))

            # Parse sub-configs based on step type
            agent: AgentSpec | None = None
            fan_out: FanOutConfig | None = None
            aggregate: AggregateConfig | None = None
            transform: TransformConfig | None = None
            condition: dict | None = None

            if step_type in (StepType.FAN_OUT, StepType.SEQUENTIAL):
                agent_raw = raw.get("agent", {})
                agent = AgentSpec(
                    task_template=agent_raw.get("task_template", ""),
                    task_type=agent_raw.get("task_type", "analysis"),
                    skills=agent_raw.get("skills", []),
                    output_schema=agent_raw.get("output_schema"),
                    agent_type=agent_raw.get("agent_type"),
                )

            if step_type == StepType.FAN_OUT:
                agent_raw_fo = raw  # fan-out config lives at step level
                fan_out = FanOutConfig(
                    over=raw.get("over", ""),
                    max_parallel=raw.get("max_parallel", 10),
                    timeout_seconds=raw.get("timeout_seconds", 1800),
                    on_failure=raw.get("on_failure", "collect_all"),
                )

            if step_type == StepType.AGGREGATE:
                aggregate = AggregateConfig(
                    input=raw.get("input", ""),
                    strategy=raw.get("strategy", "merge_arrays"),
                )

            if step_type == StepType.TRANSFORM:
                ops_raw = raw.get("operations", [])
                operations = [
                    TransformOp(
                        op=op["op"],
                        key=op.get("key"),
                        field=op.get("field"),
                        order=op.get("order", "asc"),
                        count=op.get("count"),
                        condition=op.get("condition"),
                    )
                    for op in ops_raw
                ]
                transform = TransformConfig(
                    input=raw.get("input", ""),
                    operations=operations,
                )

            if step_type == StepType.CONDITIONAL:
                condition = {
                    "condition": raw.get("condition", ""),
                    "then_step": raw.get("then_step", ""),
                    "else_step": raw.get("else_step"),
                }

            parsed.append(
                StepDefinition(
                    id=step_id,
                    type=step_type,
                    depends_on=depends_on,
                    agent=agent,
                    fan_out=fan_out,
                    aggregate=aggregate,
                    transform=transform,
                    condition=condition,
                )
            )

        return parsed

    # ------------------------------------------------------------------
    # Dependency inference
    # ------------------------------------------------------------------

    def _infer_dependencies(self, steps: list[StepDefinition]) -> list[StepDefinition]:
        """Infer implicit dependencies from ``input`` field references.

        For aggregate / transform steps, the ``input`` field may reference
        another step's output using a glob pattern like ``"search.*"``.
        The prefix before the dot (``"search"``) becomes an implicit dependency.

        Only adds dependencies that are not already declared.
        """
        step_ids = {s.id for s in steps}
        result: list[StepDefinition] = []

        for step in steps:
            input_ref: str | None = None
            if step.aggregate and step.aggregate.input:
                input_ref = step.aggregate.input
            elif step.transform and step.transform.input:
                input_ref = step.transform.input

            if input_ref:
                # Extract the step id from the reference.
                # Patterns: "search.*" -> "search", "search" -> "search"
                ref_step_id = input_ref.split(".")[0]
                if ref_step_id in step_ids and ref_step_id not in step.depends_on:
                    new_deps = list(step.depends_on) + [ref_step_id]
                    step = replace(step, depends_on=new_deps)

            result.append(step)

        return result

    # ------------------------------------------------------------------
    # DAG validation (Kahn's algorithm)
    # ------------------------------------------------------------------

    def _validate_dag(self, steps: list[StepDefinition]) -> list[StepDefinition]:
        """Topological sort using Kahn's algorithm.

        Returns steps in execution order.  Raises :class:`CompilationError`
        if the dependency graph contains a cycle.
        """
        step_map: dict[str, StepDefinition] = {s.id: s for s in steps}

        # Validate all dependencies reference existing steps
        for step in steps:
            for dep in step.depends_on:
                if dep not in step_map:
                    raise CompilationError(
                        f"Step '{step.id}' depends on unknown step '{dep}'"
                    )

        # Build in-degree map and adjacency list
        in_degree: dict[str, int] = {s.id: 0 for s in steps}
        # adjacency: parent -> children (steps that depend on parent)
        adjacency: dict[str, list[str]] = defaultdict(list)

        for step in steps:
            for dep in step.depends_on:
                adjacency[dep].append(step.id)
                in_degree[step.id] += 1

        # Start with nodes that have zero in-degree
        queue: deque[str] = deque(
            sid for sid, deg in in_degree.items() if deg == 0
        )
        sorted_ids: list[str] = []

        while queue:
            current = queue.popleft()
            sorted_ids.append(current)
            for child in adjacency[current]:
                in_degree[child] -= 1
                if in_degree[child] == 0:
                    queue.append(child)

        if len(sorted_ids) != len(steps):
            # Find the cycle members for a helpful error message
            cycle_members = [s.id for s in steps if s.id not in sorted_ids]
            raise CompilationError(
                f"Dependency cycle detected among steps: {cycle_members}"
            )

        return [step_map[sid] for sid in sorted_ids]

    # ------------------------------------------------------------------
    # Agent counting
    # ------------------------------------------------------------------

    def _count_agents(self, steps: list[StepDefinition], params: dict) -> int:
        """Count total agents that will be spawned.

        - ``fan_out``: ``len(expand_fan_out(over, params))``
        - ``sequential``: 1
        - Other step types: 0 (no agent spawned)
        """
        total = 0
        for step in steps:
            if step.type == StepType.FAN_OUT and step.fan_out:
                combos = expand_fan_out(step.fan_out.over, params)
                total += len(combos)
            elif step.type == StepType.SEQUENTIAL:
                total += 1
        return total

    # ------------------------------------------------------------------
    # Step expansion
    # ------------------------------------------------------------------

    def _expand_step(
        self,
        step: StepDefinition,
        params: dict,
        execution_id: str,
    ) -> WorkflowStep:
        """Convert a :class:`StepDefinition` to a :class:`WorkflowStep`
        suitable for persistence.

        For fan-out steps, the expanded agent specs (with interpolated task
        templates) are stored in the step's ``input`` dict.

        For sequential steps, the task template is interpolated with params.
        """
        step_uuid = uuid.uuid4().hex
        input_data: dict = {}

        if step.type == StepType.FAN_OUT and step.fan_out and step.agent:
            combos = expand_fan_out(step.fan_out.over, params)
            agents: list[dict] = []
            for i, combo in enumerate(combos):
                # Merge combo values with global params for interpolation
                merged_params = {**params, **combo}
                task = safe_interpolate(step.agent.task_template, merged_params)
                agents.append(
                    {
                        "index": i,
                        "task": task,
                        "task_type": step.agent.task_type,
                        "skills": step.agent.skills,
                        "output_schema": step.agent.output_schema,
                        "agent_type": step.agent.agent_type,
                        "params": combo,
                    }
                )
            input_data = {
                "agents": agents,
                "max_parallel": step.fan_out.max_parallel,
                "timeout_seconds": step.fan_out.timeout_seconds,
                "on_failure": step.fan_out.on_failure,
            }

        elif step.type == StepType.SEQUENTIAL and step.agent:
            task = safe_interpolate(step.agent.task_template, params)
            input_data = {
                "task": task,
                "task_type": step.agent.task_type,
                "skills": step.agent.skills,
                "output_schema": step.agent.output_schema,
                "agent_type": step.agent.agent_type,
            }

        elif step.type == StepType.AGGREGATE and step.aggregate:
            input_data = {
                "input": step.aggregate.input,
                "strategy": step.aggregate.strategy,
            }

        elif step.type == StepType.TRANSFORM and step.transform:
            input_data = {
                "input": step.transform.input,
                "operations": [
                    {
                        "op": op.op,
                        "key": op.key,
                        "field": op.field,
                        "order": op.order,
                        "count": op.count,
                        "condition": op.condition,
                    }
                    for op in step.transform.operations
                ],
            }

        elif step.type == StepType.CONDITIONAL and step.condition:
            input_data = dict(step.condition)

        return WorkflowStep(
            id=step_uuid,
            execution_id=execution_id,
            step_id=step.id,
            step_type=step.type,
            status=StepStatus.PENDING,
            input=input_data if input_data else None,
        )
