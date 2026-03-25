from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from enum import Enum


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------


class StepType(str, Enum):
    FAN_OUT = "fan_out"
    SEQUENTIAL = "sequential"
    AGGREGATE = "aggregate"
    TRANSFORM = "transform"
    REPORT = "report"
    CONDITIONAL = "conditional"


class ExecutionStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


# ---------------------------------------------------------------------------
# Step configuration dataclasses
# ---------------------------------------------------------------------------


@dataclass
class AgentSpec:
    """Specification for an agent that executes a step."""

    task_template: str  # "Search {{ source }} for events in {{ region }}"
    task_type: str = "analysis"  # matches TaskType enum
    skills: list[str] = field(default_factory=list)  # skill slugs
    output_schema: dict | None = None  # JSON Schema for expected output
    agent_type: str | None = None  # optional agent type override


@dataclass
class FanOutConfig:
    """Configuration for fan-out (parallel) step execution."""

    over: str  # "regions x sources" or "regions"
    max_parallel: int = 10
    timeout_seconds: int = 1800
    on_failure: str = "collect_all"  # "collect_all" | "fail_fast"


@dataclass
class AggregateConfig:
    """Configuration for aggregating results from prior steps."""

    input: str  # "search.*"
    strategy: str = "merge_arrays"  # merge_arrays | merge_objects | concat


@dataclass
class TransformOp:
    """A single transform operation within a transform step."""

    op: str  # deduplicate | filter | sort | limit
    key: str | None = None  # for deduplicate
    field: str | None = None  # for sort
    order: str = "asc"  # for sort
    count: int | None = None  # for limit
    condition: str | None = None  # for filter (simple field comparisons only)


@dataclass
class TransformConfig:
    """Configuration for a transform step."""

    input: str
    operations: list[TransformOp] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Step and workflow template definitions
# ---------------------------------------------------------------------------


@dataclass
class StepDefinition:
    """A single step within a workflow definition."""

    id: str
    type: StepType
    depends_on: list[str] = field(default_factory=list)
    agent: AgentSpec | None = None  # for fan_out, sequential
    fan_out: FanOutConfig | None = None  # for fan_out
    aggregate: AggregateConfig | None = None  # for aggregate
    transform: TransformConfig | None = None  # for transform
    condition: dict | None = None  # for conditional


@dataclass
class WorkflowTemplate:
    """A versioned workflow template stored in the database."""

    id: str
    slug: str
    name: str
    description: str
    version: int
    definition: dict  # raw JSON definition
    parameter_schema: dict | None = None
    is_active: bool = True
    created_by: str = ""
    created_at: str | None = None
    updated_at: str | None = None


@dataclass
class WorkflowTemplateCreate:
    """Payload for creating a new workflow template."""

    slug: str
    name: str
    description: str
    definition: dict
    parameter_schema: dict | None = None
    created_by: str = ""


@dataclass
class WorkflowTemplateUpdate:
    """Payload for updating an existing workflow template."""

    definition: dict | None = None
    parameter_schema: dict | None = None
    description: str | None = None
    changelog: str | None = None
    updated_by: str = ""


# ---------------------------------------------------------------------------
# Execution tracking
# ---------------------------------------------------------------------------


@dataclass
class WorkflowExecution:
    """Tracks a single execution of a workflow template."""

    id: str
    template_id: str
    template_version: int
    thread_id: str
    parameters: dict
    status: ExecutionStatus = ExecutionStatus.PENDING
    started_at: str | None = None
    completed_at: str | None = None
    result: dict | None = None
    error: str | None = None


@dataclass
class WorkflowStep:
    """Tracks a single step within a workflow execution."""

    id: str
    execution_id: str
    step_id: str
    step_type: StepType
    status: StepStatus = StepStatus.PENDING
    input: dict | None = None
    output: dict | None = None
    agent_jobs: list[str] = field(default_factory=list)
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None
    retry_count: int = 0


@dataclass
class CostEstimate:
    """Pre-execution cost and resource estimate for a workflow."""

    estimated_agents: int
    estimated_steps: int
    estimated_cost_usd: float
    estimated_duration_seconds: int
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Utility functions
# ---------------------------------------------------------------------------


def safe_interpolate(template: str, params: dict) -> str:
    """Simple ``{{ key }}`` replacement. NO code execution.

    Only replaces ``{{ key }}`` patterns where *key* exists in *params*.
    Unknown keys are left as-is. No nested expressions, no filters,
    no code execution.
    """

    def replacer(match: re.Match) -> str:
        key = match.group(1).strip()
        if key in params:
            return str(params[key])
        return match.group(0)  # leave unknown keys as-is

    return re.sub(r"\{\{\s*(\w+)\s*\}\}", replacer, template)


def expand_fan_out(over_expr: str, params: dict) -> list[dict]:
    """Expand a fan-out expression into individual parameter sets.

    Supports:
      ``"regions"`` -> ``[{"region": r} for r in params["regions"]]``
      ``"regions x sources"`` -> cartesian product
    """
    # The multiplication sign can be unicode x or ASCII x
    parts = [p.strip() for p in re.split(r"\s*[x\u00d7]\s*", over_expr)]

    if len(parts) == 1:
        key = parts[0]
        singular = key.rstrip("s")  # "regions" -> "region"
        values = params.get(key, [])
        return [{singular: v} for v in values]

    # Cartesian product
    arrays: list[list] = []
    keys: list[str] = []
    for part in parts:
        singular = part.rstrip("s")
        keys.append(singular)
        arrays.append(params.get(part, []))

    return [dict(zip(keys, combo)) for combo in itertools.product(*arrays)]
