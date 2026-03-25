"""Workflow Compiler — template definition to executable steps.

Compiles a workflow template definition + parameters into a flat list of
WorkflowStep objects ready for the engine to execute. Performs:

1. Parameter validation against JSON Schema (if provided)
2. Step parsing from template definition
3. Dependency inference (fan_out -> aggregate chains)
4. DAG validation via Kahn's algorithm (cycle detection)
5. Fan-out expansion using safe_interpolate / expand_fan_out
6. Agent limit enforcement

NO Jinja2, NO eval(). All interpolation uses safe_interpolate from models.
"""

from __future__ import annotations

import uuid
from collections import defaultdict, deque

from controller.workflows.models import (
    StepType,
    WorkflowStep,
    expand_fan_out,
    safe_interpolate,
)


class CompilationError(Exception):
    """Raised when a workflow template cannot be compiled."""


class WorkflowCompiler:
    """Compiles workflow template definitions into executable steps."""

    def __init__(self, max_agents_per_execution: int = 20) -> None:
        self._max_agents = max_agents_per_execution

    def compile(
        self,
        definition: dict,
        parameters: dict,
        parameter_schema: dict | None = None,
        execution_id: str | None = None,
    ) -> list[WorkflowStep]:
        """Compile a template definition into WorkflowStep objects.

        Args:
            definition: The template definition dict with a "steps" key.
            parameters: User-provided parameters for interpolation.
            parameter_schema: Optional JSON Schema for parameter validation.
            execution_id: Optional execution ID (generated if not provided).

        Returns:
            List of WorkflowStep objects ready for persistence.

        Raises:
            CompilationError: On validation failures, cycles, or limit breaches.
        """
        exec_id = execution_id or uuid.uuid4().hex

        # 1. Validate parameters
        if parameter_schema:
            self._validate_parameters(parameters, parameter_schema)

        # 2. Parse step definitions
        raw_steps = definition.get("steps", [])
        if not raw_steps:
            return []

        # 3. Infer dependencies (aggregate steps auto-depend on their input)
        step_defs = self._infer_dependencies(raw_steps)

        # 4. Validate DAG (Kahn's algorithm for cycle detection)
        self._validate_dag(step_defs)

        # 5. Expand steps into WorkflowStep objects
        total_agents = 0
        result: list[WorkflowStep] = []

        for step_def in step_defs:
            step_id = step_def["id"]
            step_type = StepType(step_def["type"])
            depends_on = list(step_def.get("depends_on", []))

            if step_type == StepType.FAN_OUT:
                step, agent_count = self._compile_fan_out(
                    step_def, parameters, exec_id, depends_on
                )
                total_agents += agent_count
            elif step_type == StepType.SEQUENTIAL:
                step = self._compile_sequential(
                    step_def, parameters, exec_id, depends_on
                )
                total_agents += 1
            elif step_type == StepType.AGGREGATE:
                step = self._compile_aggregate(
                    step_def, exec_id, depends_on
                )
            elif step_type == StepType.TRANSFORM:
                step = self._compile_transform(
                    step_def, exec_id, depends_on
                )
            elif step_type == StepType.REPORT:
                step = self._compile_report(
                    step_def, exec_id, depends_on
                )
            elif step_type == StepType.CONDITIONAL:
                step = self._compile_generic(
                    step_def, step_type, exec_id, depends_on
                )
            else:
                step = self._compile_generic(
                    step_def, step_type, exec_id, depends_on
                )

            result.append(step)

        # 6. Enforce agent limit
        if total_agents > self._max_agents:
            raise CompilationError(
                f"Workflow requires {total_agents} agents, "
                f"but the limit is {self._max_agents}"
            )

        return result

    # ------------------------------------------------------------------
    # Parameter validation
    # ------------------------------------------------------------------

    def _validate_parameters(self, params: dict, schema: dict) -> None:
        """Validate parameters against a JSON Schema subset.

        Supports: required fields, type checking (string, array, number,
        integer, boolean, object). No external dependencies.
        """
        required = schema.get("required", [])
        properties = schema.get("properties", {})

        for field in required:
            if field not in params:
                raise CompilationError(
                    f"Missing required parameter: '{field}'"
                )

        type_map = {
            "string": str,
            "array": list,
            "number": (int, float),
            "integer": int,
            "boolean": bool,
            "object": dict,
        }

        for key, prop_schema in properties.items():
            if key not in params:
                continue
            expected_type_name = prop_schema.get("type")
            if expected_type_name and expected_type_name in type_map:
                expected_type = type_map[expected_type_name]
                if not isinstance(params[key], expected_type):
                    raise CompilationError(
                        f"Parameter '{key}' must be of type "
                        f"'{expected_type_name}', got {type(params[key]).__name__}"
                    )

    # ------------------------------------------------------------------
    # Dependency inference
    # ------------------------------------------------------------------

    def _infer_dependencies(self, raw_steps: list[dict]) -> list[dict]:
        """Infer missing dependencies.

        Rules:
        - Aggregate steps with an 'input' like 'search.*' auto-depend on 'search'
        - Transform steps with an 'input' ref auto-depend on it
        - Report steps with an 'input' ref auto-depend on it
        """
        step_ids = {s["id"] for s in raw_steps}
        result = []

        for step_def in raw_steps:
            step = dict(step_def)  # shallow copy
            deps = set(step.get("depends_on", []))
            step_type = step.get("type", "")

            if step_type == "aggregate":
                agg = step.get("aggregate", {})
                input_ref = agg.get("input", "")
                base = input_ref.rstrip(".*")
                if base in step_ids and base not in deps:
                    deps.add(base)

            elif step_type == "transform":
                tf = step.get("transform", {})
                input_ref = tf.get("input", "")
                base = input_ref.rstrip(".*")
                if base in step_ids and base not in deps:
                    deps.add(base)

            elif step_type == "report":
                # Report steps may reference input in a top-level 'input' key
                input_ref = step.get("input", "")
                if isinstance(input_ref, str):
                    base = input_ref.rstrip(".*")
                    if base in step_ids and base not in deps:
                        deps.add(base)

            step["depends_on"] = list(deps)
            result.append(step)

        return result

    # ------------------------------------------------------------------
    # DAG validation (Kahn's algorithm)
    # ------------------------------------------------------------------

    def _validate_dag(self, step_defs: list[dict]) -> None:
        """Validate that the step dependency graph is a DAG (no cycles).

        Uses Kahn's algorithm for topological sorting. If not all nodes
        are processed, a cycle exists.
        """
        step_ids = {s["id"] for s in step_defs}
        in_degree: dict[str, int] = {sid: 0 for sid in step_ids}
        adjacency: dict[str, list[str]] = defaultdict(list)

        for step_def in step_defs:
            sid = step_def["id"]
            for dep in step_def.get("depends_on", []):
                if dep in step_ids:
                    adjacency[dep].append(sid)
                    in_degree[sid] += 1

        # Kahn's algorithm
        queue: deque[str] = deque()
        for sid, degree in in_degree.items():
            if degree == 0:
                queue.append(sid)

        processed = 0
        while queue:
            node = queue.popleft()
            processed += 1
            for neighbor in adjacency[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if processed != len(step_ids):
            raise CompilationError(
                "Workflow contains a dependency cycle"
            )

    # ------------------------------------------------------------------
    # Step compilers
    # ------------------------------------------------------------------

    def _compile_fan_out(
        self,
        step_def: dict,
        parameters: dict,
        execution_id: str,
        depends_on: list[str],
    ) -> tuple[WorkflowStep, int]:
        """Compile a fan-out step. Returns (step, agent_count)."""
        agent_spec = step_def.get("agent", {})
        fan_out_config = step_def.get("fan_out", {})
        over_expr = fan_out_config.get("over", "")

        # Expand fan-out into individual agent parameter sets
        expansions = expand_fan_out(over_expr, parameters) if over_expr else []

        # Build per-agent specs with interpolated tasks
        task_template = agent_spec.get("task_template", "")
        task_type = agent_spec.get("task_type", "analysis")
        skills = agent_spec.get("skills", [])
        output_schema = agent_spec.get("output_schema")

        agents: list[dict] = []
        for combo in expansions:
            merged_params = {**parameters, **combo}
            task = safe_interpolate(task_template, merged_params)
            agents.append({
                "task": task,
                "task_type": task_type,
                "skills": skills,
                "output_schema": output_schema,
                "params": combo,
            })

        step_input: dict = {
            "agents": agents,
            "max_parallel": fan_out_config.get("max_parallel", 10),
            "timeout_seconds": fan_out_config.get("timeout_seconds", 1800),
            "on_failure": fan_out_config.get("on_failure", "collect_all"),
            "depends_on": depends_on,
        }

        step = WorkflowStep(
            id=uuid.uuid4().hex,
            execution_id=execution_id,
            step_id=step_def["id"],
            step_type=StepType.FAN_OUT,
            input=step_input,
        )
        return step, len(agents)

    def _compile_sequential(
        self,
        step_def: dict,
        parameters: dict,
        execution_id: str,
        depends_on: list[str],
    ) -> WorkflowStep:
        """Compile a sequential step (single agent)."""
        agent_spec = step_def.get("agent", {})
        task_template = agent_spec.get("task_template", "")
        task = safe_interpolate(task_template, parameters)

        step_input: dict = {
            "task": task,
            "task_type": agent_spec.get("task_type", "analysis"),
            "skills": agent_spec.get("skills", []),
            "output_schema": agent_spec.get("output_schema"),
            "depends_on": depends_on,
        }

        return WorkflowStep(
            id=uuid.uuid4().hex,
            execution_id=execution_id,
            step_id=step_def["id"],
            step_type=StepType.SEQUENTIAL,
            input=step_input,
        )

    def _compile_aggregate(
        self,
        step_def: dict,
        execution_id: str,
        depends_on: list[str],
    ) -> WorkflowStep:
        """Compile an aggregate step."""
        agg = step_def.get("aggregate", {})
        step_input: dict = {
            "input": agg.get("input", ""),
            "strategy": agg.get("strategy", "merge_arrays"),
            "depends_on": depends_on,
        }

        return WorkflowStep(
            id=uuid.uuid4().hex,
            execution_id=execution_id,
            step_id=step_def["id"],
            step_type=StepType.AGGREGATE,
            input=step_input,
        )

    def _compile_transform(
        self,
        step_def: dict,
        execution_id: str,
        depends_on: list[str],
    ) -> WorkflowStep:
        """Compile a transform step."""
        tf = step_def.get("transform", {})
        operations = tf.get("operations", [])

        step_input: dict = {
            "input": tf.get("input", ""),
            "operations": [
                {
                    "op": op.get("op", ""),
                    "key": op.get("key"),
                    "field": op.get("field"),
                    "order": op.get("order", "asc"),
                    "count": op.get("count"),
                    "condition": op.get("condition"),
                }
                for op in operations
            ],
            "depends_on": depends_on,
        }

        return WorkflowStep(
            id=uuid.uuid4().hex,
            execution_id=execution_id,
            step_id=step_def["id"],
            step_type=StepType.TRANSFORM,
            input=step_input,
        )

    def _compile_report(
        self,
        step_def: dict,
        execution_id: str,
        depends_on: list[str],
    ) -> WorkflowStep:
        """Compile a report step."""
        step_input: dict = {
            "input": step_def.get("input", ""),
            "depends_on": depends_on,
        }

        return WorkflowStep(
            id=uuid.uuid4().hex,
            execution_id=execution_id,
            step_id=step_def["id"],
            step_type=StepType.REPORT,
            input=step_input,
        )

    def _compile_generic(
        self,
        step_def: dict,
        step_type: StepType,
        execution_id: str,
        depends_on: list[str],
    ) -> WorkflowStep:
        """Compile a generic/unknown step type."""
        step_input: dict = {"depends_on": depends_on}

        return WorkflowStep(
            id=uuid.uuid4().hex,
            execution_id=execution_id,
            step_id=step_def["id"],
            step_type=step_type,
            input=step_input,
        )
