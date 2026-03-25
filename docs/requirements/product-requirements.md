# Ditto Factory — Product Requirements

**Date:** 2026-03-25
**Status:** Living document — updated as ideas solidify
**Architecture decision:** Module-first (single codebase, separate API surfaces, extract later)

---

## Product Vision

Two logical products, one codebase:

1. **Agent Platform** — Consumers say what they want, agents do it, results come back with full provenance
2. **Development Platform** — Builders create, test, version, and deploy the tools/workflows/skills that agents use

---

## Product 1: Agent Platform (Ditto Factory Core)

### What exists today
- Webhook integrations (Slack, GitHub, Linear, CLI)
- Orchestrator with thread/job lifecycle
- Skill hotloading (registry, semantic search, performance tracking)
- Agent type resolution (skill requirements → Docker image)
- MCP Gateway (remote tools via SSE)
- Subagent handler (Redis pubsub, child K8s jobs)
- Tracing (structured logs + SQLite spans)
- Safety pipeline (auto-PR, anti-stall retry)
- Generalized task types (ADR-001: code_change, analysis, db_mutation, file_output, api_action)

### What's needed for the broader vision

#### R1: Two-State Workflow Engine
**Priority:** P0 — Foundation for everything else

The controller currently executes tasks as single agent invocations. We need a workflow engine that:

- **R1.1** Accepts a structured intent (from the intent classifier) and compiles it into a deterministic execution plan
- **R1.2** Executes steps sequentially or in parallel (fan-out / fan-in)
- **R1.3** Each step spawns one focused, single-purpose agent (no agent-to-agent communication)
- **R1.4** Agents have no awareness of the broader workflow — they receive a task, do it, exit
- **R1.5** Step results flow back to the engine, which decides the next step deterministically
- **R1.6** Supports: sequential, parallel (fan-out/fan-in), conditional branching, retry with backoff
- **R1.7** Workflow state persisted in Postgres/SQLite (survives controller restart)
- **R1.8** JSON-based workflow definitions (Step Functions ASL-inspired, stored in DB)
- **R1.9** Feature-flagged, backwards-compatible (single-step workflows = current behavior)

#### R2: Intent Classifier
**Priority:** P0 — Turns natural language into structured workflow input

- **R2.1** LLM-based: takes user request, outputs structured intent (goal, parameters, constraints)
- **R2.2** Intent maps to a workflow template from the template library
- **R2.3** If no template matches, falls back to single-agent execution (current behavior)
- **R2.4** Intent schema is typed and validated (not freeform JSON)

#### R3: Workflow Template Library
**Priority:** P0 — Pre-built patterns that the engine compiles

- **R3.1** Templates are parameterized workflow definitions (e.g., `geo-search` takes regions + sources)
- **R3.2** Stored in Postgres, versioned, soft-deletable (same pattern as skill registry)
- **R3.3** Templates reference agent types and skills by ID, not inline logic
- **R3.4** CRUD API for templates (`/api/v1/templates/...`)
- **R3.5** Ship with at least 2 starter templates for testing: `single-task` (current behavior) and `fan-out-search`

#### R4: Observability + Provenance
**Priority:** P1 — Users need to see what agents did and where data came from

- **R4.1** Every workflow step produces a trace span (extends existing tracing)
- **R4.2** Every data result links back to: source URL, search query, agent ID, timestamp
- **R4.3** Users can query: "show me what agents did for this request"
- **R4.4** Users can query: "where did this specific result come from?"
- **R4.5** Workflow execution timeline visible via API (not just logs)

#### R5: Scalable Fan-Out
**Priority:** P1 — The "now do it for the entire world" requirement

- **R5.1** Workflow engine can spawn N agents in parallel (configurable limit)
- **R5.2** Results merge deterministically (engine handles, not agents)
- **R5.3** Deduplication of merged results (configurable strategy: exact match, fuzzy, semantic)
- **R5.4** Partial failure handling: if 3 of 10 agents fail, return results from 7 + error report
- **R5.5** Cost controls: max agents per workflow, max total runtime, budget limits

#### R6: Result Aggregation
**Priority:** P1 — Merge, deduplicate, and structure outputs from multiple agents

- **R6.1** Aggregation is a deterministic workflow step (not agent reasoning)
- **R6.2** Supports: merge, deduplicate, filter, sort, format
- **R6.3** Output format configurable: JSON, CSV, structured report
- **R6.4** Results stored in `task_artifacts` table (from ADR-001)

#### R7: User-Facing Transparency
**Priority:** P2 — "Show me what you did"

- **R7.1** Users can see workflow steps and their status (pending, running, complete, failed)
- **R7.2** Users can see which tools/sources each agent used
- **R7.3** Users can save a successful workflow as a named template for replay
- **R7.4** Users can modify parameters and re-run ("add Facebook as a source")
- **R7.5** Users can cancel a running workflow

---

## Product 2: Development Platform (Ditto Factory Dev)

### What exists today
- Skill CRUD API with versioning, rollback, semantic search
- Agent type registry
- Performance tracker (usage metrics, success rates, trend analysis)
- Seed skills + seed script

### What's needed

#### D1: Tool Development + Registry
**Priority:** P0 — Tools are what agents use to interact with the world

- **D1.1** Tools are versioned, testable units (e.g., "Google Events Scraper v2")
- **D1.2** Tool CRUD API with version history and rollback
- **D1.3** Tools declare: inputs, outputs, rate limits, cost, error modes
- **D1.4** Tools map to MCP server endpoints or skill instructions
- **D1.5** Tools can be tested in isolation (dry-run mode with sample inputs)

#### D2: A/B Testing Agent Task Runs
**Priority:** P1 — Compare workflow versions, skill versions, tool versions

- **D2.1** Run the same task against two configurations (A vs B)
- **D2.2** Compare: success rate, speed, cost, result quality
- **D2.3** Quality scoring: automated (schema compliance, dedup rate) + manual (user thumbs up/down)
- **D2.4** Results feed back into performance tracker

#### D3: Approval + Deployment Pipeline
**Priority:** P1 — Gate changes before they reach the agent platform

- **D3.1** New tools/skills/templates start in "draft" status
- **D3.2** Promotion: draft → staging → production (requires approval)
- **D3.3** Staging runs against real data but results don't go to users
- **D3.4** Rollback: one-click revert to previous version
- **D3.5** Audit log: who deployed what, when, why

#### D4: Workflow Template Development
**Priority:** P1 — Build and test workflow templates

- **D4.1** Visual or structured editor for workflow templates (API-first, UI later)
- **D4.2** Template validation: type-check step inputs/outputs, verify referenced tools exist
- **D4.3** Template dry-run: execute against mock data, verify step flow
- **D4.4** Template versioning (same pattern as skills)

#### D5: Analytics Dashboard
**Priority:** P2 — Understand platform health and usage

- **D5.1** Per-tool metrics: usage, success rate, latency, cost
- **D5.2** Per-workflow metrics: completion rate, avg duration, cost per run
- **D5.3** Per-skill metrics: injection frequency, correlation with task success
- **D5.4** Trend analysis: week-over-week changes
- **D5.5** Alerting: tool failure rate spikes, cost anomalies

---

## Cross-Cutting Concerns

#### X1: Agent Contracts
**Priority:** P0 — The handoff between workflow engine and agent must be well-defined

- **X1.1** Every agent receives: task description, skills, tool access, output schema
- **X1.2** Every agent returns: structured result matching the output schema, provenance metadata, exit code
- **X1.3** The contract is the same regardless of task type (coding, scraping, analysis)
- **X1.4** Agents are stateless — no memory of previous runs, no awareness of other agents

#### X2: Cost Management
**Priority:** P1

- **X2.1** Per-workflow cost tracking (API tokens + compute)
- **X2.2** Configurable budgets per user, per workflow type
- **X2.3** Cost estimation before execution ("this will use ~$2.50")
- **X2.4** Kill switch: abort workflow if cost exceeds budget

#### X3: Multi-Tenancy
**Priority:** P2 — When other teams use the platform

- **X3.1** Org-scoped tools, skills, templates, workflows
- **X3.2** Usage quotas per org
- **X3.3** Data isolation (results from org A not visible to org B)

---

## What's NOT in Scope (for now)

- **Agent-to-agent communication** — Workflow engine coordinates, agents don't talk to each other
- **Long-running agents** — Agents are ephemeral, single-task. No persistent sessions.
- **User-facing UI** — API-first. UI comes later.
- **Real-time streaming** — Results delivered on completion, not streamed during execution
- **Custom agent code** — Users don't write code. They configure tools, skills, and workflows through the API.

---

## Implementation Priority (Lean Path)

Goal: Get to a state where Ted can test end-to-end.

### Wave 1: Foundation (~2 weeks)
- R1 (Workflow Engine) — core step executor with sequential + parallel
- R3 (Template Library) — DB-backed templates, CRUD API, 2 starters
- X1 (Agent Contracts) — formalize input/output schema

### Wave 2: Intelligence (~2 weeks)
- R2 (Intent Classifier) — LLM maps user request → template + params
- R4 (Observability) — extend tracing to workflow steps
- R6 (Result Aggregation) — merge step for fan-out workflows

### Wave 3: Scale (~2 weeks)
- R5 (Scalable Fan-Out) — N agents in parallel with cost controls
- D1 (Tool Registry) — versioned tools with dry-run testing
- R7 (User Transparency) — "show me what you did" API

### Wave 4: Dev Platform (~ongoing)
- D2-D5 — A/B testing, approval pipeline, workflow editor, analytics

---

## Open Questions

1. **Where does the LLM intent classification run?** In the controller (adds latency to every request) or as a pre-processing step?
2. **What's the workflow state storage?** Same Postgres/SQLite as everything else, or a dedicated workflow state store?
3. **How do fan-out agents share a repo branch?** Or do non-code tasks not need git at all?
4. **What's the result quality signal?** Automated schema validation? User feedback? Both?
5. **Should workflow templates support loops?** (e.g., "keep searching until you find 100 events") Or is that too close to Turing-complete?
