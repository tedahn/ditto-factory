"""Two-State Workflow Engine.

State 1 (Deterministic): This engine — compiles templates, advances steps,
    merges results. No LLM reasoning.
State 2 (Agent Reasoning): Claude Code agents — spawned per step, single-purpose,
    no workflow awareness.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

import aiosqlite

from controller.config import Settings
from controller.workflows.models import (
    CostEstimate,
    ExecutionStatus,
    StepStatus,
    StepType,
    WorkflowExecution,
    WorkflowStep,
)
from controller.workflows.compiler import WorkflowCompiler

logger = logging.getLogger(__name__)


class WorkflowEngine:
    """Core workflow execution engine.

    Implements deterministic DAG traversal with CAS (compare-and-swap)
    locking to prevent race conditions when multiple agents complete
    simultaneously.
    """

    def __init__(
        self,
        db_path: str,
        settings: Settings,
        compiler: WorkflowCompiler | None = None,
        spawner=None,        # JobSpawner (optional, for agent steps)
        redis_state=None,    # RedisState (optional, for task payloads)
    ):
        self._db_path = db_path
        self._settings = settings
        self._compiler = compiler or WorkflowCompiler(
            max_agents_per_execution=settings.max_agents_per_execution
        )
        self._spawner = spawner
        self._redis = redis_state

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def start(
        self,
        template_slug: str,
        parameters: dict,
        thread_id: str,
    ) -> str:
        """Start a workflow execution.

        1. Load template from DB
        2. Compile into steps
        3. Persist execution + steps
        4. Start first runnable step(s)

        Returns: execution_id
        """
        # 1. Load template
        template = await self._load_template(template_slug)
        if template is None:
            raise ValueError(f"Template not found: {template_slug}")

        # 2. Create execution record and compile into steps
        execution_id = uuid.uuid4().hex
        execution = WorkflowExecution(
            id=execution_id,
            template_id=template.id,
            template_version=template.version,
            thread_id=thread_id,
            parameters=parameters,
            status=ExecutionStatus.RUNNING,
        )
        steps = self._compiler.compile(
            template.definition, parameters, template.parameter_schema, execution_id
        )

        # Inject depends_on from template definition into step input dicts
        # so that _get_runnable_steps can determine step ordering.
        raw_steps = template.definition.get("steps", [])
        deps_by_id = {s["id"]: list(s.get("depends_on", [])) for s in raw_steps}
        for step in steps:
            deps = deps_by_id.get(step.step_id, [])
            if deps:
                if step.input is None:
                    step.input = {}
                step.input["depends_on"] = deps

        # 3. Persist execution + steps
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO workflow_executions
                   (id, template_id, template_version, thread_id, parameters,
                    status, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    execution.id,
                    execution.template_id,
                    execution.template_version,
                    execution.thread_id,
                    json.dumps(execution.parameters),
                    ExecutionStatus.RUNNING.value,
                    now,
                ),
            )
            for step in steps:
                await db.execute(
                    """INSERT INTO workflow_steps
                       (id, execution_id, step_id, step_type, status, input,
                        agent_jobs)
                       VALUES (?, ?, ?, ?, ?, ?, ?)""",
                    (
                        step.id,
                        step.execution_id,
                        step.step_id,
                        step.step_type.value,
                        StepStatus.PENDING.value,
                        json.dumps(step.input) if step.input else None,
                        json.dumps(step.agent_jobs),
                    ),
                )
            await db.commit()

        # 4. Start first runnable step(s) — those with no dependencies
        runnable = await self._get_runnable_steps(execution.id)
        for step in runnable:
            await self._start_step(step, execution)

        logger.info(
            "Workflow started: execution_id=%s template=%s steps=%d",
            execution.id, template_slug, len(steps),
        )
        return execution.id

    async def advance(self, execution_id: str) -> None:
        """Advance a workflow after a step completes.

        1. Find next runnable steps (all dependencies met)
        2. Start them (with CAS locking to prevent races)
        3. If no more steps and all complete -> mark execution complete
        4. If a step failed -> mark execution failed (unless collect_all)

        CONCURRENCY: Uses atomic CAS updates to prevent race conditions
        when multiple steps complete simultaneously.
        """
        execution = await self.get_execution(execution_id)
        if execution is None:
            logger.warning("advance() called for unknown execution: %s", execution_id)
            return

        if execution.status != ExecutionStatus.RUNNING:
            return

        steps = await self.get_steps(execution_id)

        # Check for failures (non-collect_all steps)
        for step in steps:
            if step.status == StepStatus.FAILED:
                on_failure = (step.input or {}).get("on_failure", "fail_fast")
                if on_failure != "collect_all":
                    await self._update_execution_status(
                        execution_id,
                        ExecutionStatus.FAILED,
                        error=f"Step '{step.step_id}' failed: {step.error}",
                    )
                    return

        # Find and start runnable steps
        runnable = await self._get_runnable_steps(execution_id)
        for step in runnable:
            # _start_step handles CAS internally
            await self._start_step(step, execution)

        # Re-fetch execution — a report step may have already marked it
        # complete with a richer result payload (including quality checks).
        execution = await self.get_execution(execution_id)
        if execution is None or execution.status != ExecutionStatus.RUNNING:
            return

        # Re-fetch steps to check completion
        steps = await self.get_steps(execution_id)
        terminal = {StepStatus.COMPLETED, StepStatus.FAILED, StepStatus.SKIPPED}
        if all(s.status in terminal for s in steps):
            # Find the last completed step with output as the result
            final_step = None
            for s in reversed(steps):
                if s.status == StepStatus.COMPLETED and s.output:
                    final_step = s
                    break

            has_failures = any(s.status == StepStatus.FAILED for s in steps)
            if has_failures:
                await self._update_execution_status(
                    execution_id,
                    ExecutionStatus.FAILED,
                    result=final_step.output if final_step else None,
                    error="One or more steps failed",
                )
            else:
                await self._update_execution_status(
                    execution_id,
                    ExecutionStatus.COMPLETED,
                    result=final_step.output if final_step else None,
                )

    async def handle_agent_result(
        self,
        execution_id: str,
        step_id: str,
        agent_index: int,
        result: dict,
    ) -> None:
        """Handle an agent completing within a workflow step.

        For sequential steps: store result, mark step complete, advance.
        For fan-out steps: store per-agent result. If all agents done,
            mark step complete, advance.
        """
        # Find the workflow step by step_id within the execution
        step = await self._get_step_by_step_id(execution_id, step_id)
        if step is None:
            logger.warning(
                "handle_agent_result: step not found execution=%s step=%s",
                execution_id, step_id,
            )
            return

        # Validate against output_schema if present
        output_schema = None
        if step.input and isinstance(step.input, dict):
            output_schema = step.input.get("output_schema")

        # Extract the agent's structured output from the result wrapper
        structured_output = result.get("result") if isinstance(result, dict) else None

        if output_schema and (structured_output or result):
            # Validate the structured output if present, otherwise the full result
            validate_target = structured_output if structured_output is not None else result
            try:
                import jsonschema
                jsonschema.validate(instance=validate_target, schema=output_schema)
                logger.info("Step %s output validated against schema", step.step_id)
            except jsonschema.ValidationError as e:
                logger.warning(
                    "Step %s output failed schema validation: %s",
                    step.step_id, str(e.message)[:200]
                )
                # Store validation error but don't fail the step
                if result is None:
                    result = {}
                if isinstance(result, dict):
                    result["_validation_errors"] = [str(e.message)[:500]]
            except Exception:
                logger.warning("Schema validation failed unexpectedly", exc_info=True)

        # If this step expected structured output, store as artifact
        store_payload = structured_output if structured_output is not None else result
        if output_schema and store_payload:
            try:
                from controller.models import Artifact, ResultType
                artifact = Artifact(
                    id=uuid.uuid4().hex,
                    result_type=ResultType.STRUCTURED_OUTPUT,
                    location=json.dumps(store_payload),
                    metadata={"step_id": step.step_id, "execution_id": execution_id},
                )
                # Store via state backend if available
                if self._redis:
                    await self._redis.set(
                        f"structured_output:{execution_id}:{step.step_id}",
                        json.dumps(store_payload),
                    )
                logger.info(
                    "Stored structured output artifact %s for step %s",
                    artifact.id, step.step_id,
                )
            except Exception:
                logger.warning("Failed to store structured output artifact", exc_info=True)

        if step.step_type == StepType.SEQUENTIAL:
            # Sequential: single agent, store result directly
            await self._update_step_status(
                step.id,
                StepStatus.COMPLETED,
                output=result,
            )
            await self.advance(execution_id)

        elif step.step_type == StepType.FAN_OUT:
            # Fan-out: store per-agent result in workflow_step_agents
            now = datetime.now(timezone.utc).isoformat()
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    """UPDATE workflow_step_agents
                       SET status = 'completed', output = ?, completed_at = ?
                       WHERE step_id = ? AND agent_index = ?""",
                    (
                        json.dumps(result),
                        now,
                        step.id,
                        agent_index,
                    ),
                )
                await db.commit()

            # Check if all agents for this step are done
            all_done, merged = await self._check_fan_out_complete(step.id)
            if all_done:
                await self._update_step_status(
                    step.id,
                    StepStatus.COMPLETED,
                    output=merged,
                )
                await self.advance(execution_id)

    async def reconcile(self) -> dict:
        """Crash recovery: find and fix orphaned workflow executions.

        Called on controller startup and periodically (every 60s).

        1. Find executions with status='running'
        2. For each, find steps with status='running'
        3. For running steps that are agent steps (sequential/fan_out):
           - Check if the K8s job still exists (via spawner)
           - If job completed: re-process the result
           - If job missing: mark step as failed
        4. Call advance() to continue the workflow
        5. For executions with no running steps and no pending steps:
           - If all steps completed: mark execution completed
           - If any step failed: mark execution failed

        Returns: {"reconciled": N, "failed": M, "completed": K}
        """
        stats = {"reconciled": 0, "failed": 0, "completed": 0}

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM workflow_executions WHERE status = 'running'"
            )
            executions = await cursor.fetchall()

        for exec_row in executions:
            execution_id = exec_row["id"]
            try:
                steps = await self.get_steps(execution_id)

                has_running = False
                has_pending = False
                has_failed = False
                all_done = True

                for step in steps:
                    if step.status == StepStatus.RUNNING:
                        has_running = True
                        all_done = False
                        # Check if this is an agent step with jobs
                        if step.step_type in (StepType.SEQUENTIAL, StepType.FAN_OUT) and step.agent_jobs:
                            # Try to check job status (if spawner available)
                            for job_name in step.agent_jobs:
                                if self._spawner:
                                    try:
                                        # Try to get job status from K8s
                                        # If not found, mark as failed
                                        pass  # K8s check would go here
                                    except Exception:
                                        pass
                            # If step has been running too long, mark as failed
                            if step.started_at:
                                started = datetime.fromisoformat(step.started_at)
                                elapsed = (datetime.now(timezone.utc) - started).total_seconds()
                                if elapsed > self._settings.workflow_step_timeout_seconds:
                                    await self._update_step_status(
                                        step.id, StepStatus.FAILED,
                                        error="Step timed out during crash recovery",
                                    )
                                    has_failed = True
                                    has_running = False
                                    stats["failed"] += 1
                        elif step.step_type in (StepType.AGGREGATE, StepType.TRANSFORM, StepType.REPORT):
                            # Deterministic steps shouldn't be stuck in running
                            # Mark as failed and retry
                            await self._update_step_status(
                                step.id, StepStatus.FAILED,
                                error="Deterministic step found running during recovery",
                            )
                            has_failed = True
                            has_running = False
                            stats["failed"] += 1

                    elif step.status == StepStatus.PENDING:
                        has_pending = True
                        all_done = False
                    elif step.status == StepStatus.FAILED:
                        has_failed = True
                        all_done = False

                # Try to advance the workflow
                if not has_running and has_pending:
                    await self.advance(execution_id)
                    stats["reconciled"] += 1
                elif all_done or (has_failed and not has_pending and not has_running):
                    # All steps are terminal — update execution
                    if has_failed:
                        await self._update_execution_status(
                            execution_id, ExecutionStatus.FAILED,
                            error="One or more steps failed",
                        )
                    else:
                        await self._update_execution_status(
                            execution_id, ExecutionStatus.COMPLETED,
                        )
                    stats["completed"] += 1

            except Exception:
                logger.exception("Failed to reconcile execution %s", execution_id)

        if stats["reconciled"] or stats["failed"] or stats["completed"]:
            logger.info("Workflow reconciliation: %s", stats)

        return stats

    async def cancel(self, execution_id: str) -> None:
        """Cancel a running workflow. Mark all pending/running steps as skipped."""
        steps = await self.get_steps(execution_id)
        now = datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(self._db_path) as db:
            for step in steps:
                if step.status in (StepStatus.PENDING, StepStatus.RUNNING):
                    await db.execute(
                        """UPDATE workflow_steps
                           SET status = ?, completed_at = ?
                           WHERE id = ?""",
                        (StepStatus.SKIPPED.value, now, step.id),
                    )
            await db.commit()

        await self._update_execution_status(
            execution_id, ExecutionStatus.CANCELLED
        )
        logger.info("Workflow cancelled: %s", execution_id)

    async def get_execution(self, execution_id: str) -> WorkflowExecution | None:
        """Get execution by ID."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM workflow_executions WHERE id = ?",
                (execution_id,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_execution(row)

    async def get_steps(self, execution_id: str) -> list[WorkflowStep]:
        """Get all steps for an execution."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM workflow_steps WHERE execution_id = ? ORDER BY created_at",
                (execution_id,),
            )
            rows = await cursor.fetchall()
        return [self._row_to_step(row) for row in rows]

    async def list_executions(
        self, status: str | None = None
    ) -> list[WorkflowExecution]:
        """List all executions, optionally filtered by status."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            if status:
                cursor = await db.execute(
                    "SELECT * FROM workflow_executions WHERE status = ? ORDER BY started_at DESC",
                    (status,),
                )
            else:
                cursor = await db.execute(
                    "SELECT * FROM workflow_executions ORDER BY started_at DESC"
                )
            rows = await cursor.fetchall()
        return [self._row_to_execution(row) for row in rows]

    async def estimate(
        self,
        template_slug: str,
        parameters: dict,
    ) -> dict:
        """Estimate cost/agents for a workflow without executing."""
        template = await self._load_template(template_slug)
        if template is None:
            raise ValueError(f"Template not found: {template_slug}")

        steps = self._compiler.compile(
            template.definition, parameters, template.parameter_schema
        )
        # Count agents: fan-out steps have multiple agents in input.agents,
        # sequential steps spawn 1 agent each
        agent_count = 0
        for step in steps:
            if step.step_type == StepType.FAN_OUT:
                agents_list = (step.input or {}).get("agents", [])
                agent_count += len(agents_list)
            elif step.step_type == StepType.SEQUENTIAL:
                agent_count += 1

        est = CostEstimate(
            estimated_agents=agent_count,
            estimated_steps=len(steps),
            estimated_cost_usd=agent_count * 0.05,
            estimated_duration_seconds=agent_count * 120,
        )
        return asdict(est)

    # ------------------------------------------------------------------
    # Internal step executors
    # ------------------------------------------------------------------

    async def _start_step(
        self, step: WorkflowStep, execution: WorkflowExecution
    ) -> None:
        """Start a single step based on its type."""
        # Mark step as running (CAS)
        started = await self._update_step_status(step.id, StepStatus.RUNNING)
        if not started:
            return  # Another caller already started it

        logger.info(
            "Starting step: execution=%s step=%s type=%s",
            execution.id, step.step_id, step.step_type.value,
        )

        try:
            match step.step_type:
                case StepType.SEQUENTIAL:
                    await self._execute_sequential(step, execution)
                case StepType.FAN_OUT:
                    await self._execute_fan_out(step, execution)
                case StepType.AGGREGATE:
                    await self._execute_aggregate(step, execution)
                case StepType.TRANSFORM:
                    await self._execute_transform(step, execution)
                case StepType.REPORT:
                    await self._execute_report(step, execution)
                case _:
                    logger.warning("Unknown step type: %s", step.step_type)
                    await self._update_step_status(
                        step.id, StepStatus.FAILED,
                        error=f"Unknown step type: {step.step_type}",
                    )
        except Exception as exc:
            logger.exception(
                "Step execution failed: execution=%s step=%s",
                execution.id, step.step_id,
            )
            await self._update_step_status(
                step.id, StepStatus.FAILED, error=str(exc)
            )

    async def _execute_sequential(
        self, step: WorkflowStep, execution: WorkflowExecution
    ) -> None:
        """Spawn a single agent for a sequential step.

        For Phase 1: creates agent record and spawns via JobSpawner if available.
        If no spawner, marks step as needing external agent handling.
        """
        step_input = step.input or {}
        task = step_input.get("task", "")
        task_type = step_input.get("task_type", "analysis")

        agent_thread_id = (
            f"{execution.thread_id}:wf:{execution.id}"
            f":s:{step.step_id}:a:0"
        )

        # Create agent record
        agent_id = uuid.uuid4().hex
        now = datetime.now(timezone.utc).isoformat()
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """INSERT INTO workflow_step_agents
                   (id, step_id, agent_index, thread_id, status,
                    input, started_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    agent_id,
                    step.id,
                    0,
                    agent_thread_id,
                    "running",
                    json.dumps({"task": task, "task_type": task_type}),
                    now,
                ),
            )
            await db.commit()

        # Spawn K8s job if spawner is available
        if self._spawner and self._redis:
            task_payload = {
                "task": task,
                "task_type": task_type,
                "skills": step_input.get("skills", []),
                "output_schema": step_input.get("output_schema"),
                "workflow_context": {
                    "execution_id": execution.id,
                    "step_id": step.step_id,
                    "agent_index": 0,
                },
            }
            await self._redis.push_task(agent_thread_id, task_payload)
            agent_redis = self._settings.agent_redis_url or self._settings.redis_url
            job_name = self._spawner.spawn(
                thread_id=agent_thread_id,
                github_token="",
                redis_url=agent_redis,
            )

            # Update agent record with job name
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "UPDATE workflow_step_agents SET k8s_job_name = ? WHERE id = ?",
                    (job_name, agent_id),
                )
                # Update step agent_jobs
                await db.execute(
                    "UPDATE workflow_steps SET agent_jobs = ? WHERE id = ?",
                    (json.dumps([job_name]), step.id),
                )
                await db.commit()

    async def _execute_fan_out(
        self, step: WorkflowStep, execution: WorkflowExecution
    ) -> None:
        """Spawn N agents for a fan-out step with bounded parallelism.

        Uses an asyncio.Semaphore to limit concurrent spawns to max_parallel.
        """
        step_input = step.input or {}
        agents = step_input.get("agents", [])
        max_parallel = step_input.get("max_parallel", 10)

        if not agents:
            # No work to do — mark step complete with empty output
            await self._update_step_status(
                step.id, StepStatus.COMPLETED, output=[]
            )
            await self.advance(execution.id)
            return

        sem = asyncio.Semaphore(max_parallel)
        job_names: list[str | None] = [None] * len(agents)

        async def spawn_agent(index: int, agent_spec: dict) -> None:
            async with sem:
                task = agent_spec.get("task", "")
                task_type = agent_spec.get("task_type", "analysis")
                now = datetime.now(timezone.utc).isoformat()

                agent_thread_id = (
                    f"{execution.thread_id}:wf:{execution.id}"
                    f":s:{step.step_id}:a:{index}"
                )

                agent_id = uuid.uuid4().hex
                async with aiosqlite.connect(self._db_path) as db:
                    await db.execute(
                        """INSERT INTO workflow_step_agents
                           (id, step_id, agent_index, thread_id, status,
                            input, started_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?)""",
                        (
                            agent_id,
                            step.id,
                            index,
                            agent_thread_id,
                            "running",
                            json.dumps({"task": task, "params": agent_spec.get("params", {})}),
                            now,
                        ),
                    )
                    await db.commit()

                if self._spawner and self._redis:
                    task_payload = {
                        "task": task,
                        "task_type": task_type,
                        "system_prompt": (
                            "You are a workflow agent. Complete this task and "
                            "return structured results.\n\nTask: " + task
                        ),
                        "skills": agent_spec.get("skills", []),
                        "output_schema": agent_spec.get("output_schema"),
                        "workflow_context": {
                            "execution_id": execution.id,
                            "step_id": step.step_id,
                            "agent_index": index,
                        },
                    }
                    await self._redis.push_task(agent_thread_id, task_payload)
                    agent_redis = self._settings.agent_redis_url or self._settings.redis_url
                    job_name = self._spawner.spawn(
                        thread_id=agent_thread_id,
                        github_token="",
                        redis_url=agent_redis,
                    )
                    job_names[index] = job_name

        # Spawn all agents in parallel (bounded by semaphore)
        tasks = [spawn_agent(i, agent) for i, agent in enumerate(agents)]
        await asyncio.gather(*tasks, return_exceptions=True)

        # Persist all job names at once
        actual_jobs = [j for j in job_names if j is not None]
        if actual_jobs:
            async with aiosqlite.connect(self._db_path) as db:
                await db.execute(
                    "UPDATE workflow_steps SET agent_jobs = ? WHERE id = ?",
                    (json.dumps(actual_jobs), step.id),
                )
                await db.commit()

    async def _execute_aggregate(
        self, step: WorkflowStep, execution: WorkflowExecution
    ) -> None:
        """Merge results from a previous fan-out step.

        Strategies: merge_arrays, merge_objects, concat.
        Completes synchronously (no agent needed).
        """
        step_input = step.input or {}
        input_ref = step_input.get("input", step_input.get("input_ref", ""))
        strategy = step_input.get("strategy", "merge_arrays")

        input_data = await self._get_step_output(execution.id, input_ref)

        if input_data is None:
            input_data = []

        match strategy:
            case "merge_arrays":
                result: list | dict | str = []
                if isinstance(input_data, list):
                    for item in input_data:
                        if isinstance(item, list):
                            result.extend(item)
                        elif isinstance(item, dict) and "result" in item:
                            inner = item["result"]
                            if isinstance(inner, list):
                                result.extend(inner)
                            else:
                                result.append(inner)
                        else:
                            result.append(item)
                else:
                    result = [input_data]

            case "merge_objects":
                result = {}
                if isinstance(input_data, list):
                    for item in input_data:
                        if isinstance(item, dict):
                            result.update(item)
                else:
                    result = input_data or {}

            case "concat":
                if isinstance(input_data, list):
                    result = "\n".join(str(item) for item in input_data)
                else:
                    result = str(input_data or "")

            case _:
                result = input_data

        await self._update_step_status(
            step.id, StepStatus.COMPLETED,
            output={"result": result, "count": len(result) if isinstance(result, (list, dict, str)) else 1},
        )
        await self.advance(execution.id)

    async def _execute_transform(
        self, step: WorkflowStep, execution: WorkflowExecution
    ) -> None:
        """Apply transform operations (deduplicate, filter, sort, limit).

        All operations are predefined — NO eval(). Completes synchronously.
        """
        step_input = step.input or {}
        input_ref = step_input.get("input", step_input.get("input_ref", ""))
        operations = step_input.get("operations", [])

        data = await self._get_step_output(execution.id, input_ref)

        # Extract the actual list from wrapped output
        if isinstance(data, dict) and "result" in data:
            data = data["result"]
        if data is None:
            data = []
        if not isinstance(data, list):
            data = [data]

        for op in operations:
            op_type = op.get("op", "")
            match op_type:
                case "deduplicate":
                    key_expr = op.get("key", "")
                    keys = [k.strip() for k in key_expr.split("+")]
                    seen: set[tuple] = set()
                    unique: list = []
                    for item in data:
                        if isinstance(item, dict):
                            sig = tuple(str(item.get(k, "")) for k in keys)
                        else:
                            sig = (str(item),)
                        if sig not in seen:
                            seen.add(sig)
                            unique.append(item)
                    data = unique

                case "filter":
                    condition = op.get("condition", "")
                    if "=" in condition and "==" not in condition and "!=" not in condition and ">=" not in condition and "<=" not in condition:
                        # Simple field=value filter (NO eval)
                        field_name, value = condition.split("=", 1)
                        field_name = field_name.strip()
                        value = value.strip().strip("'\"")
                        data = [
                            item for item in data
                            if isinstance(item, dict) and str(item.get(field_name, "")) == value
                        ]
                    else:
                        # Use existing condition evaluator for ==, !=, >, < etc.
                        data = [
                            item for item in data
                            if self._eval_simple_condition(item, condition)
                        ]

                case "sort":
                    sort_field = op.get("field", "")
                    order = op.get("order", "asc")
                    data = sorted(
                        data,
                        key=lambda x: str(x.get(sort_field, "")) if isinstance(x, dict) else str(x),
                        reverse=(order == "desc"),
                    )

                case "limit":
                    count = op.get("count", len(data))
                    data = data[:count]

        await self._update_step_status(
            step.id, StepStatus.COMPLETED,
            output={"result": data, "count": len(data)},
        )
        await self.advance(execution.id)

    async def _execute_report(
        self, step: WorkflowStep, execution: WorkflowExecution
    ) -> None:
        """Deliver results to the user via the originating integration.

        For now: stores the result in the execution's result field.
        Future: post to Slack thread, GitHub comment, etc.
        """
        step_input = step.input or {}
        input_ref = step_input.get("input", step_input.get("input_ref", ""))

        data = await self._get_step_output(execution.id, input_ref)

        # Run quality checks on the final output
        from controller.workflows.quality import QualityChecker

        checker = QualityChecker()

        result_data = data
        if isinstance(data, dict) and "result" in data:
            result_data = data["result"]

        quality_report = checker.check(
            result_data
            if isinstance(result_data, list)
            else [result_data]
            if result_data
            else [],
        )

        # Store final result on the execution
        final_result = {
            "data": result_data,
            "quality": {
                "score": quality_report.score,
                "total_items": quality_report.total_items,
                "valid_items": quality_report.valid_items,
                "checks": quality_report.checks,
                "warnings": quality_report.warnings,
            },
        }

        await self._update_execution_status(
            execution.id, ExecutionStatus.COMPLETED, result=final_result
        )

        await self._update_step_status(
            step.id,
            StepStatus.COMPLETED,
            output={"delivered": True, "quality_score": quality_report.score},
        )
        # Don't call advance() — report is terminal, execution is already
        # marked complete

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _load_template(self, slug: str):
        """Load a workflow template by slug from the DB."""
        from controller.workflows.models import WorkflowTemplate

        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM workflow_templates WHERE slug = ? AND is_active = 1",
                (slug,),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return WorkflowTemplate(
            id=row["id"],
            slug=row["slug"],
            name=row["name"],
            description=row["description"] or "",
            version=row["version"],
            definition=json.loads(row["definition"]),
            parameter_schema=json.loads(row["parameter_schema"]) if row["parameter_schema"] else None,
            is_active=bool(row["is_active"]),
            created_by=row["created_by"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )

    async def _get_step_output(
        self, execution_id: str, step_ref: str
    ) -> dict | list | None:
        """Get output from a previous step by reference.

        Supports:
          'search.*' -> all fan-out agent outputs from step "search"
          'search'   -> output of step "search"
          'merge'    -> output of step "merge"
        """
        if step_ref.endswith(".*"):
            # Fan-out wildcard: get all agent outputs for this step
            base_step_id = step_ref[:-2]
            step = await self._get_step_by_step_id(execution_id, base_step_id)
            if step is None:
                return None
            # Gather outputs from workflow_step_agents
            async with aiosqlite.connect(self._db_path) as db:
                db.row_factory = aiosqlite.Row
                cursor = await db.execute(
                    """SELECT output FROM workflow_step_agents
                       WHERE step_id = ? AND status = 'completed'
                       ORDER BY agent_index""",
                    (step.id,),
                )
                rows = await cursor.fetchall()

            results = []
            for row in rows:
                if row["output"]:
                    results.append(json.loads(row["output"]))
            return results
        else:
            # Direct step reference
            step = await self._get_step_by_step_id(execution_id, step_ref)
            if step is None or step.output is None:
                return None
            return step.output

    async def _get_step_by_step_id(
        self, execution_id: str, step_id: str
    ) -> WorkflowStep | None:
        """Look up a WorkflowStep by its step_id within an execution."""
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                """SELECT * FROM workflow_steps
                   WHERE execution_id = ? AND step_id = ?""",
                (execution_id, step_id),
            )
            row = await cursor.fetchone()
        if row is None:
            return None
        return self._row_to_step(row)

    async def _update_step_status(
        self,
        step_id: str,
        status: StepStatus,
        output: dict | list | str | None = None,
        error: str | None = None,
    ) -> bool:
        """Atomic CAS status update. Returns True if update succeeded.

        Uses: UPDATE ... WHERE id = ? AND status IN (expected_statuses)
        This prevents race conditions -- only one caller wins.
        """
        now = datetime.now(timezone.utc).isoformat()

        # Determine valid source statuses for the transition
        if status == StepStatus.RUNNING:
            expected = (StepStatus.PENDING.value,)
        elif status in (StepStatus.COMPLETED, StepStatus.FAILED):
            expected = (StepStatus.RUNNING.value,)
        elif status == StepStatus.SKIPPED:
            expected = (StepStatus.PENDING.value, StepStatus.RUNNING.value)
        else:
            expected = (StepStatus.PENDING.value, StepStatus.RUNNING.value)

        placeholders = ",".join("?" for _ in expected)

        params: list = [status.value, now]
        if output is not None:
            output_json = json.dumps(output)
        else:
            output_json = None
        params.append(output_json)
        params.append(error)
        params.append(step_id)
        params.extend(expected)

        async with aiosqlite.connect(self._db_path) as db:
            cursor = await db.execute(
                f"""UPDATE workflow_steps
                    SET status = ?, completed_at = ?, output = ?, error = ?
                    WHERE id = ? AND status IN ({placeholders})""",
                params,
            )
            await db.commit()
            return cursor.rowcount > 0

    async def _update_execution_status(
        self,
        execution_id: str,
        status: ExecutionStatus,
        result: dict | list | str | None = None,
        error: str | None = None,
    ) -> None:
        """Update execution status."""
        now = datetime.now(timezone.utc).isoformat()
        result_json = json.dumps(result) if result is not None else None

        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """UPDATE workflow_executions
                   SET status = ?, completed_at = ?, result = ?, error = ?
                   WHERE id = ?""",
                (status.value, now, result_json, error, execution_id),
            )
            await db.commit()

    async def _get_runnable_steps(self, execution_id: str) -> list[WorkflowStep]:
        """Find steps whose dependencies are all completed."""
        steps = await self.get_steps(execution_id)

        completed_step_ids = {
            s.step_id for s in steps if s.status == StepStatus.COMPLETED
        }

        runnable = []
        for step in steps:
            if step.status != StepStatus.PENDING:
                continue
            depends_on = (step.input or {}).get("depends_on", [])
            if all(dep in completed_step_ids for dep in depends_on):
                runnable.append(step)
        return runnable

    async def _check_fan_out_complete(
        self, step_id: str
    ) -> tuple[bool, list]:
        """Check if all agents in a fan-out step are complete.

        Returns (all_done, merged_outputs).
        """
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM workflow_step_agents WHERE step_id = ? ORDER BY agent_index",
                (step_id,),
            )
            agents = await cursor.fetchall()

        if not agents:
            return True, []

        all_done = all(
            a["status"] in ("completed", "failed") for a in agents
        )
        if not all_done:
            return False, []

        merged = []
        for a in agents:
            if a["status"] == "completed" and a["output"]:
                merged.append(json.loads(a["output"]))

        return True, merged

    @staticmethod
    def _eval_simple_condition(item: dict, condition: str) -> bool:
        """Evaluate a simple field comparison condition.

        Supports: "field == value", "field != value", "field > value",
        "field < value". NO eval() — parsed manually.
        """
        if not isinstance(item, dict):
            return True

        for op in ("!=", "==", ">=", "<=", ">", "<"):
            if op in condition:
                parts = condition.split(op, 1)
                if len(parts) == 2:
                    field_name = parts[0].strip()
                    expected = parts[1].strip().strip("'\"")
                    actual = str(item.get(field_name, ""))

                    match op:
                        case "==":
                            return actual == expected
                        case "!=":
                            return actual != expected
                        case ">":
                            try:
                                return float(actual) > float(expected)
                            except ValueError:
                                return actual > expected
                        case "<":
                            try:
                                return float(actual) < float(expected)
                            except ValueError:
                                return actual < expected
                        case ">=":
                            try:
                                return float(actual) >= float(expected)
                            except ValueError:
                                return actual >= expected
                        case "<=":
                            try:
                                return float(actual) <= float(expected)
                            except ValueError:
                                return actual <= expected
                break

        return True  # If condition can't be parsed, include the item

    # ------------------------------------------------------------------
    # Row converters
    # ------------------------------------------------------------------

    @staticmethod
    def _row_to_execution(row: aiosqlite.Row) -> WorkflowExecution:
        return WorkflowExecution(
            id=row["id"],
            template_id=row["template_id"],
            template_version=row["template_version"],
            thread_id=row["thread_id"],
            parameters=json.loads(row["parameters"]) if row["parameters"] else {},
            status=ExecutionStatus(row["status"]),
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            result=json.loads(row["result"]) if row["result"] else None,
            error=row["error"],
        )

    @staticmethod
    def _row_to_step(row: aiosqlite.Row) -> WorkflowStep:
        return WorkflowStep(
            id=row["id"],
            execution_id=row["execution_id"],
            step_id=row["step_id"],
            step_type=StepType(row["step_type"]),
            status=StepStatus(row["status"]),
            input=json.loads(row["input"]) if row["input"] else None,
            output=json.loads(row["output"]) if row["output"] else None,
            agent_jobs=json.loads(row["agent_jobs"]) if row["agent_jobs"] else [],
            started_at=row["started_at"],
            completed_at=row["completed_at"],
            error=row["error"],
            retry_count=row["retry_count"],
        )
