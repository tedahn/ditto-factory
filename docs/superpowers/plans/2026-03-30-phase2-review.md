# Phase 2 Architecture Review: Workflow Onboarding Plan

**Date:** 2026-03-30  
**Reviewer:** Software Architect Agent  
**Status:** REVISIONS NEEDED — 2 critical blockers, plan can be simplified

---

## Question 1: Does the engine support spawning agent pods in Docker local dev?

**Finding: CRITICAL BLOCKER — No Docker spawner exists.**

The `_execute_sequential` method calls `self._spawner.spawn()` which delegates to `JobSpawner.spawn()` — this calls `self._batch_api.create_namespaced_job()` using the Kubernetes `BatchV1Api`. In Docker Compose, there is no Kubernetes API server.

The engine does have a guard (`if self._spawner and self._redis`), meaning if no spawner is injected, it skips the spawn. But then the step just... hangs. There is no fallback execution path — no Docker spawner, no subprocess spawner, no in-process execution.

**Recommendation:** Before Phase 2, implement a `LocalSpawner` that uses `docker compose run` or `subprocess` to execute the agent container. This is a prerequisite, not a task that can be deferred. Without it, the workflow cannot execute agent steps locally.

---

## Question 2: Can the engine handle structured output today?

**Finding: NO — `output_schema` exists in the plan but is not enforced anywhere.**

The `AgentSpec` concept references `output_schema` in step input, and `_execute_sequential` passes it through the task payload to Redis:

```python
task_payload = {
    "task": task,
    "output_schema": step_input.get("output_schema"),
    "workflow_context": {...},
}
```

But `handle_agent_result` receives a raw `result: dict` and does no schema validation or JSON parsing. The `ResultType` enum today has no `STRUCTURED_OUTPUT` value. The agent communication path is: agent writes result to Redis, controller picks it up via `handle_agent_result`. The result dict is stored directly in `step.output` as JSON text.

**Recommendation:** Task 1 (add STRUCTURED_OUTPUT) is correctly scoped. But also add JSON Schema validation in `handle_agent_result` — compare result against the step's `output_schema` before storing. Without validation, garbage-in propagates to the import step.

---

## Question 3: Is the 3-step workflow necessary?

**Finding: The 3-step design is over-engineered for the actual need.**

The plan defines:
1. `analyze` (SEQUENTIAL/agent) — clone + analyze + produce manifest JSON
2. `validate-manifest` (TRANSFORM) — validate JSON schema
3. `import-toolkit` (SEQUENTIAL) — import into registry

Steps 2 and 3 don't need agents. Step 2 is a TRANSFORM (pure function on data). Step 3 is called "sequential" but is really a controller-side operation — no agent reasoning is needed to call `registry.import_from_manifest()`.

The engine's TRANSFORM step type runs a `transform_fn` — but looking at `_execute_transform`, it resolves the function from a string path and calls it. This works but requires registering the validation function.

**Recommendation:** Reduce to 2 steps:
1. `analyze` (SEQUENTIAL/agent) — clone, analyze, produce manifest JSON
2. `validate-and-import` (TRANSFORM) — validate JSON + import into registry (single Python function)

This still dogfoods the workflow engine (template compilation, agent spawn, result handling, transform step) while eliminating unnecessary complexity.

---

## Question 4: What's missing end-to-end?

**Gaps between "workflow starts" and "toolkit appears in registry":**

| Gap | Severity | Notes |
|-----|----------|-------|
| No Docker/local spawner | CRITICAL | Agent steps cannot execute locally |
| No STRUCTURED_OUTPUT ResultType | HIGH | Agent can't return structured data |
| No output_schema validation | MEDIUM | Invalid manifests would propagate |
| Agent result return path unclear | HIGH | Agent writes to Redis via what mechanism? The agent needs a "completion reporter" that posts results back to the controller's `handle_agent_result` endpoint |
| No webhook/callback from agent to controller | HIGH | `handle_agent_result` is called... by whom? There must be a Redis subscriber or webhook listener that bridges agent completion to the engine |
| TRANSFORM step function registration | MEDIUM | `_execute_transform` resolves functions from string paths — the validate+import function must be importable |
| Skill injection into agent pod | MEDIUM | The "toolkit-analysis" skill must be mounted via the loadout system (Phase 1). Is that path tested? |
| Template registration at startup | LOW | Built-in templates need a registration mechanism |

**Recommendation:** The agent-to-controller result path is the most underspecified part. Trace the exact code path: agent completes -> ??? -> `handle_agent_result()` gets called. This is likely the `subagent.py` Redis pubsub listener, but the plan never mentions it.

---

## Question 5: Is there a simpler path?

**Finding: Yes. The plan has 7 tasks; it can be done in 4.**

Revised task list:

| # | Task | Rationale |
|---|------|-----------|
| 1 | **LocalSpawner for Docker dev** | Critical blocker. Implement `LocalSpawner` that runs agent containers via `docker compose run`. Inject via settings. | 
| 2 | **STRUCTURED_OUTPUT + validation** | Add ResultType, add JSON Schema validation in `handle_agent_result`, add output_schema to step metadata |
| 3 | **Onboarding template + transform function** | 2-step template (analyze agent + validate-and-import transform). Register as built-in. Includes the analysis skill as an inline prompt, not a separate skill file. |
| 4 | **API endpoint + frontend wiring** | POST `/api/toolkits/onboard` that calls `engine.start("toolkit-onboarding", ...)`. Frontend shows async status. |

Eliminated tasks:
- **Task 3 (toolkit-analysis skill)** — Inline the analysis prompt in the template's agent task_template. A separate skill file adds a dependency on the skill injection pipeline, which may not be working locally yet.
- **Task 7 (install git in controller)** — The plan itself notes this is unnecessary (agent pod has git, not controller).
- **Task 4 (OnboardingHandler as separate class)** — Fold into the transform function. A separate handler class with special wiring into `handle_agent_result` is coupling the engine to a specific use case. A TRANSFORM step is the engine's built-in extensibility mechanism — use it.

---

## Question 6: How does the agent get the skill?

**Finding: The loadout/skill injection path is unverified for local dev.**

Phase 1 defined `AgentLoadout` — skills mounted into agent pods via ConfigMap or init container. This is a Kubernetes concept. In Docker Compose, there is no ConfigMap. The skill injection path likely does not work locally.

**Recommendation:** For Phase 2, avoid the skill injection dependency entirely. Put the analysis instructions directly in the agent's `task_template` string within the workflow template definition. The template already supports `{{ analysis_skill }}` interpolation — just make that a long prompt string in the parameters, or hardcode it in the template. This is pragmatic: get the workflow working end-to-end first, then optimize with proper skill injection later.

---

## Critical Blockers (must resolve before implementation)

1. **No local agent execution path.** The spawner is K8s-only. Without a `LocalSpawner`, no agent step can run in Docker Compose. This blocks all workflow testing.

2. **Agent-to-controller result path is unspecified.** The plan assumes `handle_agent_result` gets called but never explains the mechanism. Is there a Redis subscriber in the controller that listens for agent completions and calls `handle_agent_result`? This must be traced and documented before building on top of it.

---

## Revised Execution Order

```
Task 1: LocalSpawner          (blocks everything)
Task 2: STRUCTURED_OUTPUT     (blocks Task 3)
Task 3: Template + Transform  (blocks Task 4)  
Task 4: API + Frontend        (ships the feature)
```

Estimated effort: ~3 days instead of ~5 days. Still dogfoods the workflow engine properly (template compilation, agent spawn, result handling, transform steps, advance/completion).
