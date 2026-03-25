# Q5: Should Workflow Templates Support Loops?

## Status
Recommendation: **Option A (No loops) for MVP, with Option C (Conditional continuation) as the planned evolution path.**

## Problem

Users will want iterative behavior in workflows: "search until you find 100 events," "paginate through results," "retry until success." The naive implementation is a loop construct, but loops make workflows Turing-complete, introducing infinite-loop risk, unpredictable cost, and untestable termination conditions.

The core design principle -- **workflows are deterministic, agents are reasoning** -- gives us the answer, but we need to be precise about where the boundary falls.

## Options Evaluated

### Option A: No Loops, Ever (DAG-only)
- Workflows are directed acyclic graphs. No cycles.
- Iterative behavior lives inside the agent (pagination, retries).
- **Pro**: Always terminates. Cost is bounded by step count. Simplest engine. Trivial to test.
- **Con**: Pushes complexity into agent reasoning, which is what workflows are supposed to reduce.

### Option B: Bounded Loops
- Allow loops with mandatory `max_iterations`, `timeout`, and `cost_limit`.
- **Pro**: Covers iteration use cases directly. Bounded worst-case.
- **Con**: Three new parameters to configure correctly. Loop body interactions with fan-out create combinatorial complexity. Engine must track loop state, which complicates pause/resume. Still Turing-complete within bounds -- a bounded Turing machine is still harder to reason about than a DAG.

### Option C: Conditional Continuation
- No explicit loop construct. A step can signal "continue" or "done."
- Engine appends another copy of the step(s), up to `max_expansions`.
- **Pro**: Each iteration is a visible, debuggable step in the execution plan. Cost is predictable (max_expansions * step_cost). Semantically cleaner than loops -- the workflow "grows" rather than "cycles."
- **Con**: More complex than pure DAG. Requires the engine to handle dynamic plan expansion. Still needs a bound.

### Option D: Reactive / Human-in-the-Loop
- Workflows are one-shot. Insufficient results go back to the user.
- **Pro**: Simplest. No runaway risk.
- **Con**: Unusable for automated pipelines. Terrible UX for common cases like pagination.

## Analysis

### Most "loop" use cases are not loops

| Scenario | Actual Pattern | Loop Needed? |
|----------|---------------|--------------|
| "Find 100 events" (pagination) | Agent-internal pagination | No -- agent handles it in one step |
| "Search all cities in Texas" | Fan-out over known list | No -- static fan-out |
| "Monitor this source daily" | Scheduler / cron trigger | No -- external trigger, not a loop |
| "Retry failed agents" | Engine-level retry policy | No -- retry is infrastructure, not workflow logic |
| "Enrich each result with details" | Map step (fan-out over dynamic results) | No -- fan-out over previous step's output |
| "Keep searching until quality threshold" | Conditional continuation | Maybe -- but rare at MVP stage |

**Key insight**: Of the six common scenarios, five are cleanly handled without any loop construct. The sixth (quality-threshold iteration) is the only genuine loop, and it is rare enough to defer.

### The minimum construct that covers 90% of use cases

No loop construct at all. Instead:

1. **Agent-internal pagination**: The agent's tool calls handle pagination. The workflow step says "find 100 events" and the agent pages through results. This is reasoning, not orchestration.
2. **Fan-out**: The workflow engine supports `fan_out: previous_step.results`, creating parallel steps from a dynamic list. This is deterministic expansion, not a loop.
3. **Retry policy**: Each step has `max_retries: 3` at the engine level. This is infrastructure.

These three mechanisms cover 90%+ of real use cases without introducing any loop construct.

### Cost management

| Approach | Cost Predictability |
|----------|-------------------|
| DAG (Option A) | Exact: `sum(step_costs)` -- known before execution |
| Bounded loop (Option B) | Worst-case: `sum(step_costs) + loop_body_cost * max_iterations` |
| Continuation (Option C) | Worst-case: `sum(step_costs) + continued_step_cost * max_expansions` |
| Reactive (Option D) | Exact per run, but total depends on user behavior |

Option A is the only one where cost is fully deterministic before execution. This matters for a system where users set budgets.

### Termination proofs

- **Option A**: Trivially terminates. DAGs have no cycles. Every step runs at most once (or fan_out times).
- **Option B**: Terminates if bounds are enforced, but the engine must be trusted to enforce them correctly under all failure modes (what if the counter update fails?).
- **Option C**: Same termination argument as B, slightly simpler because there is no mutable counter -- just a step count.
- **Option D**: Trivially terminates.

## Recommendation

### MVP: Option A (No loops)

Ship a pure DAG engine with these three mechanisms instead of loops:

```yaml
# 1. Agent-internal pagination (agent handles iteration)
steps:
  - id: find_events
    agent: search_agent
    input:
      query: "tech conferences in Austin"
      min_results: 100  # Agent will paginate internally
    max_retries: 2      # 2. Engine-level retry on failure

# 3. Fan-out over dynamic results
  - id: enrich_events
    agent: enrichment_agent
    fan_out: find_events.results  # One agent per result
    input:
      event: "{{item}}"
```

### Post-MVP: Option C (Conditional continuation)

If real user demand emerges for workflow-level iteration (not agent-level), add conditional continuation with these constraints:

```yaml
steps:
  - id: deep_search
    agent: search_agent
    continuation:
      condition: "results.count < target_count"
      max_expansions: 5          # Hard limit, mandatory
      cost_limit: "$2.00"        # Budget cap
      input_transform: "next_page(previous.cursor)"
```

This is NOT a loop -- it is plan expansion. Each continuation creates a new, visible step in the execution trace. The workflow plan grows from 3 steps to at most 8 steps, and every step is individually observable, debuggable, and costed.

### What NOT to build

- **Unbounded loops**: Never. No `while(true)` in workflow templates.
- **Nested loops**: Even bounded, these create combinatorial explosion.
- **Loop-with-fan-out**: A loop body that fans out is O(iterations * fan_out_size). Dangerous.

## How Common "Loop" Scenarios Work Without Loops

### Scenario 1: "Find 100 events"
```yaml
steps:
  - id: search
    agent: google_search_agent
    input:
      query: "tech events Austin 2026"
      min_results: 100
    # Agent internally: search page 1, check count, search page 2, ...
    # Workflow sees: one step, one result set
```
The agent's tool interface supports pagination. The workflow does not need to know about pages.

### Scenario 2: "Search all cities in Texas"
```yaml
steps:
  - id: get_cities
    agent: data_agent
    input:
      query: "list all major cities in Texas"
  - id: search_per_city
    agent: search_agent
    fan_out: get_cities.results
    input:
      query: "events in {{item}}"
```
This is fan-out, not a loop. All cities are searched in parallel.

### Scenario 3: "Retry if the agent fails"
```yaml
steps:
  - id: search
    agent: search_agent
    max_retries: 3
    retry_backoff: exponential
    input:
      query: "tech events"
```
Retry is engine infrastructure. The workflow template does not need loop syntax for this.

### Scenario 4: "Monitor daily"
```yaml
# This is a TRIGGER, not a loop
triggers:
  - schedule: "0 9 * * *"  # Daily at 9am

steps:
  - id: check_source
    agent: monitor_agent
    input:
      url: "https://example.com/events"
```
Scheduling is external to the workflow. Each trigger creates a new workflow execution.

### Scenario 5: "Progressively refine results" (the genuine loop case)
```yaml
# POST-MVP: Conditional continuation
steps:
  - id: search
    agent: search_agent
    continuation:
      condition: "results.quality_score < 0.8"
      max_expansions: 3
    input:
      query: "{{previous.refined_query | default: original_query}}"
```
This is the one case that genuinely benefits from workflow-level iteration. Defer it until real demand validates the need.

## Decision Summary

| Decision | Rationale |
|----------|-----------|
| MVP ships with DAG-only (no loops) | Simplest, always terminates, cost is exact |
| Agent handles pagination internally | Pagination is reasoning, not orchestration |
| Engine provides retry as infrastructure | Retry is cross-cutting, not workflow logic |
| Fan-out covers "do X for each Y" | Deterministic expansion, not iteration |
| Conditional continuation is the post-MVP path | Covers the remaining 10% without Turing-completeness |
| Unbounded loops are permanently off the table | No business case justifies infinite-loop risk |
