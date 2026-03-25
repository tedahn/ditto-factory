# Agent Workflow Orchestration Patterns in Production

Research report on how production AI agent platforms separate deterministic control flow from agent reasoning.

**Date:** 2026-03-22
**Sources:** Anthropic engineering blog, Cognition/Devin docs, LangGraph blog, AutoGen README, CrewAI Flows docs, Temporal SDK docs, Claude Code docs

---

## 1. Anthropic's Foundational Taxonomy: Workflows vs. Agents

Anthropic draws a clear architectural line between two categories of agentic systems (source: "Building Effective Agents", Dec 2024):

- **Workflows**: LLMs and tools orchestrated through **predefined code paths**. The developer controls the flow.
- **Agents**: LLMs **dynamically direct their own processes** and tool usage. The model controls the flow.

Their key insight: *"The most successful implementations weren't using complex frameworks. Instead, they were building with simple, composable patterns."*

### Workflow Patterns (Deterministic Control Flow)

| Pattern | Description | When to Use |
|---------|-------------|-------------|
| **Prompt Chaining** | Sequential LLM calls, each processes previous output. Programmatic gates between steps. | Task decomposes into fixed subtasks. Trade latency for accuracy. |
| **Routing** | Classify input, direct to specialized handler. | Distinct categories handled separately. |
| **Parallelization** | Run subtasks simultaneously (sectioning) or same task multiple times (voting). | Independent subtasks; need diverse perspectives. |
| **Orchestrator-Workers** | Central LLM dynamically breaks task into subtasks, delegates to worker LLMs. | Tasks where subtasks can't be predicted in advance. |
| **Evaluator-Optimizer** | One LLM generates, another evaluates in a loop. | Clear evaluation criteria; iterative refinement adds value. |

### Agent Pattern (Dynamic Control Flow)

When to use agents: *"when flexibility and model-driven decision-making are needed at scale."*

The autonomous agent uses an **agentic loop**: the model decides which tools to call, processes results, and continues until it determines the task is complete. This is the pattern Claude Code itself uses internally.

### The Two-Layer System in Claude Code

Claude Code uses a layered approach to constrain and extend the agent:

1. **CLAUDE.md instructions** -- Static, deterministic context injected at session start. Sets coding standards, architecture decisions, and constraints the agent must follow.
2. **Skills (custom slash commands)** -- Prompt-based meta-tools that inject domain-specific instructions into the conversation context. A `/review-pr` skill doesn't execute code; it modifies *how Claude reasons* about the next request. Skills are prompt expansion, not function calling.
3. **Hooks** -- Shell commands triggered before/after Claude Code actions (e.g., auto-format after file edit, lint before commit). Purely deterministic; the agent doesn't control them.
4. **Sub-agents** -- Multiple Claude Code instances working on subtasks simultaneously. A lead agent coordinates, assigns subtasks, and merges results.

**Architecture insight:** Claude Code separates concerns by layer:
- **Hooks** = deterministic pre/post processing (no LLM involved)
- **Skills** = prompt injection that shapes reasoning (LLM-driven but template-constrained)
- **Sub-agents** = parallel agent execution (LLM-driven, orchestrated by a lead agent)
- **CLAUDE.md** = persistent constraints on all reasoning

This is NOT a two-state system. It's a **layered constraint system** where deterministic layers (hooks, CLAUDE.md) bound the non-deterministic core (agent reasoning).

---

## 2. Devin / Cognition Labs

Based on public information (GA announcement, Dec 2024):

### Architecture (Inferred from Behavior)

- **Interface-first design**: Slack is the primary interface. Users tag `@devin` with tasks.
- **Session-based execution**: Each task runs in an isolated session with its own browser, terminal, and editor.
- **Knowledge system**: Persistent "Knowledge" items that Devin learns from feedback. These are coaching instructions that persist across sessions -- analogous to CLAUDE.md but learned dynamically.
- **Structured I/O via API**: The Devin API supports structured input/output for repetitive tasks, suggesting a separation between task definition (structured) and task execution (agent reasoning).

### Control Flow Model

| Aspect | How Devin Handles It |
|--------|---------------------|
| **Control flow definition** | User-defined in natural language via Slack/IDE. No YAML or code-based workflow definition. |
| **Task scoping** | Users are advised to "keep sessions under ~3 hours and break down large tasks." Scoping is manual. |
| **Result flow** | Git commits + PR creation. Devin responds to GitHub PR comments automatically. |
| **Agent constraints** | "Give Devin tasks that you know how to do yourself." "Tell Devin how to test or check its own work." Constraints are social, not technical. |

### Key Observation

Devin appears to use **pure agent reasoning** for orchestration -- no visible workflow engine. The "deterministic" layer is thin: session isolation, Knowledge items, and the structured API for repetitive tasks. The failure mode they've observed: sessions over 3 hours tend to go off-track, suggesting that purely agentic orchestration degrades over long horizons.

---

## 3. CrewAI: The Clearest Two-State Implementation

CrewAI's **Flows** feature is the most explicit implementation of the two-state pattern found in this research.

### Architecture

```
Flow (Deterministic Python)           Crew (Agent Reasoning)
================================      ========================
@start() -> method_a()          --->  crew.kickoff() inside
@listen(method_a) -> method_b() --->  any flow method
@router(method_b) -> "success"/"fail"
@listen("success") -> method_c()
@listen("fail") -> method_d()
```

### How It Works

1. **Flows** are Python classes with decorator-based control flow:
   - `@start()` -- marks the entry point
   - `@listen(method)` -- triggers when a method completes
   - `@router(method)` -- conditional branching based on return value
   - `@or_()` / `@and_()` -- boolean logic for combining triggers

2. **Crews** (agent teams) are invoked *inside* flow methods. The flow decides WHEN to call agents; the agents decide HOW to complete the task.

3. **State management**: Flows have explicit state (Pydantic models or unstructured dicts) that flows through the pipeline. Agent outputs are written back to flow state.

### Handoff Between States

```python
class CodeReviewFlow(Flow[ReviewState]):
    @start()
    def fetch_pr(self):
        # DETERMINISTIC: fetch PR data via API
        self.state.pr_data = github_api.get_pr(self.state.pr_number)

    @listen(fetch_pr)
    def review_code(self):
        # AGENT REASONING: crew analyzes the code
        crew = CodeReviewCrew()
        result = crew.kickoff(inputs={"code": self.state.pr_data})
        self.state.review = result

    @router(review_code)
    def decide_action(self):
        # DETERMINISTIC: route based on structured output
        if self.state.review.score > 8:
            return "approve"
        return "request_changes"
```

### What Goes Wrong

- **State leakage**: If agent reasoning modifies flow state in unexpected ways, downstream deterministic steps break.
- **Error handling**: When a crew fails mid-execution, the flow needs to decide whether to retry, skip, or abort. This is a deterministic decision about a non-deterministic process.
- **Context bloat**: Crews that receive too much state perform worse. Scoping inputs to the minimum needed is critical.

---

## 4. LangGraph: State Machine Approach

LangGraph models multi-agent systems as **directed graphs** where:

- **Nodes** = agent functions or sub-graphs
- **Edges** = control flow (can be conditional)
- **State** = shared object that flows through the graph

### Multi-Agent Patterns

| Pattern | Description | Control Flow |
|---------|-------------|--------------|
| **Supervisor** | One agent routes to worker agents. Workers have independent scratchpads; final responses go to global state. | Agent-decided routing |
| **Hierarchical Teams** | Supervisors whose workers are themselves LangGraph sub-graphs. | Nested agent-decided routing |
| **Shared Scratchpad** | All agents see each other's work on a common state. | Round-robin or conditional edges |

### Key Design Decision

LangGraph explicitly frames multi-agent systems as **state machines**:

> "The independent agent nodes become the states, and how those agents are connected is the transition matrices. Since a state machine can be viewed as a labeled, directed graph, we will think of these things in the same way."

### Control Flow Definition

Control flow is defined in **Python code** as graph construction:

```python
graph = StateGraph(AgentState)
graph.add_node("researcher", research_agent)
graph.add_node("coder", coding_agent)
graph.add_conditional_edges("supervisor", route_function, {
    "research": "researcher",
    "code": "coder",
    "FINISH": END
})
```

The `route_function` can be deterministic (if/else on state) or agent-driven (LLM decides the route). This is where the two-state boundary lives in LangGraph -- **the edge function**.

### What the Agent Cannot Do

Workers in a supervisor pattern cannot:
- Modify the global scratchpad directly (only append their response)
- Call other workers (only the supervisor routes)
- Change the graph topology at runtime

---

## 5. Microsoft AutoGen: Conversation-Centric Orchestration

AutoGen models multi-agent systems as **conversations between agents**.

### Architecture

| Concept | Description |
|---------|-------------|
| **Teams** | Groups of agents with a termination condition |
| **RoundRobinGroupChat** | Agents take turns, share context |
| **SelectorGroupChat** | An LLM selects which agent speaks next |
| **AgentTool** | Wraps an agent as a tool, callable by another agent |

### The Two-State Pattern in AutoGen

AutoGen separates orchestration from reasoning through **team types**:

- **Deterministic orchestration**: `RoundRobinGroupChat` -- fixed order, no LLM decides who goes next.
- **Agent-driven orchestration**: `SelectorGroupChat` -- an LLM picks the next speaker based on conversation state.
- **Hybrid**: An outer agent uses `AgentTool` to call specialized agents as tools. The outer agent reasons about WHAT to delegate; the inner agent reasons about HOW to execute.

### Result Flow

Agents communicate by appending messages to a shared conversation. The `TaskResult` object captures the final state. Termination is handled by conditions (e.g., `TextMentionTermination` -- stop when "APPROVE" appears in output).

---

## 6. Temporal: Durable Execution for Agent Workflows

Temporal is not an agent framework, but its **durable execution** model is increasingly used as the orchestration layer for agent systems.

### Why Temporal for Agents

| Problem | Temporal's Solution |
|---------|-------------------|
| Agent task takes hours | Workflow execution survives process crashes |
| Need to retry failed steps | Built-in retry policies per activity |
| Need to pause for human review | Signals and queries on running workflows |
| Need to coordinate multiple agents | Child workflows with parent close policies |

### Architecture Pattern

```
Temporal Workflow (Deterministic)     Activities (Can be Agent Calls)
===================================   ==============================
def agent_pipeline(task):             @activity.defn
  plan = await plan_task(task)        async def plan_task(task):
  for step in plan.steps:               # Call LLM to plan
    result = await execute(step)         return llm.plan(task)
    if not validate(result):
      result = await retry(step)      @activity.defn
  return aggregate(results)           async def execute_step(step):
                                         # Agent reasoning happens here
                                         return agent.run(step)
```

### Key Properties

- **Workflows must be deterministic** -- no random, no network calls, no side effects. This is enforced by the runtime.
- **Activities are where non-deterministic work happens** -- including agent reasoning, API calls, LLM invocations.
- **The handoff is explicit**: workflow code calls `await workflow.execute_activity(...)`. The activity runs in a separate process.

### What Goes Wrong

- **Replay sensitivity**: If the workflow definition changes while executions are in-flight, replay breaks. Temporal requires explicit versioning (`workflow.patched()`) for workflow changes.
- **Timeout tuning**: Agent activities can take unpredictable time. Setting activity timeouts too low kills successful-but-slow agent runs; too high wastes resources on stuck agents.

---

## 7. The Two-State Pattern: Cross-Cutting Analysis

### Definition

The two-state pattern separates:

- **State 1 (Deterministic Orchestration)**: Decides WHAT to do, in what ORDER, with what INPUTS. Defined in code, YAML, or database. No LLM reasoning.
- **State 2 (Agent Reasoning)**: Executes a single focused task. Receives scoped context. Returns structured results. The LLM has autonomy WITHIN the task but cannot change the overall flow.

### Where Each Platform Falls

| Platform | Orchestration Layer | Agent Layer | Handoff Mechanism |
|----------|-------------------|-------------|-------------------|
| **Claude Code** | Hooks + CLAUDE.md (constraints) | Agentic loop (tool selection) | Layered constraints, not explicit handoff |
| **Devin** | User instructions + Knowledge | Full agent autonomy per session | Session boundary IS the handoff |
| **CrewAI** | Flows (`@start`, `@listen`, `@router`) | Crews (agent teams) | `crew.kickoff(inputs=...)` inside flow methods |
| **LangGraph** | Graph edges (conditional functions) | Node functions (agents) | Edge functions route between nodes |
| **AutoGen** | Team type (RoundRobin, Selector) | Individual agents | Message passing in shared conversation |
| **Temporal** | Workflow definition (deterministic code) | Activities (agent calls) | `execute_activity()` with retry policies |

### Common Failure Modes

#### When Agents Have Too Much Autonomy (Orchestration)

1. **Goal drift**: Agent wanders from the original task over long horizons (Devin's 3-hour limit).
2. **Infinite loops**: Agent keeps trying the same failing approach without escalating.
3. **Context pollution**: Agent accumulates irrelevant context that degrades reasoning quality.
4. **Resource exhaustion**: Unbounded tool calls burn tokens/compute with no progress.
5. **Inconsistent outputs**: Without structured output constraints, downstream consumers can't parse results.

#### When Workflows Are Too Rigid

1. **Brittle to edge cases**: Predefined paths can't handle unexpected inputs or errors.
2. **Over-decomposition**: Breaking tasks too finely forces agents into artificially constrained reasoning.
3. **State explosion**: Trying to anticipate all possible paths creates unmanageable workflow graphs.
4. **Replay fragility**: Changing workflow definitions breaks in-flight executions (Temporal's versioning problem).
5. **Loss of emergent problem-solving**: The agent can't discover a better approach because the workflow won't let it deviate.

### The Sweet Spot

The most successful production systems share these characteristics:

1. **Deterministic orchestration at the task level** -- what to do, in what order.
2. **Agent autonomy at the step level** -- how to complete each individual step.
3. **Structured interfaces between layers** -- typed inputs/outputs at the handoff boundary.
4. **Explicit constraint propagation** -- the orchestration layer tells the agent what it CANNOT do.
5. **Timeout and retry policies** -- deterministic fallback for non-deterministic execution.

---

## 8. Implications for Ditto Factory

Based on this research, the key architectural decisions for a workflow engine are:

### Recommended Pattern

Use the **CrewAI Flows model** as conceptual inspiration: deterministic Python orchestration that invokes agent reasoning at well-defined points. This maps directly to:

- **Orchestrator** = Flow (deterministic, state machine, decides what/when/order)
- **Agent worker** = Activity (autonomous within scope, receives typed input, returns typed output)

### Specific Recommendations

1. **Define workflows in code, not YAML** -- Python decorators or function composition. This gives type safety, testability, and IDE support.

2. **Scope agent context aggressively** -- Each agent invocation should receive the minimum context needed. Pass specific file paths, not "the whole repo."

3. **Require structured output from agents** -- Use Pydantic models or JSON schemas for agent results. This makes the handoff between deterministic and non-deterministic layers clean.

4. **Implement timeout + retry at the orchestration layer** -- The agent doesn't decide whether to retry; the workflow does, based on the structured error output.

5. **Make the constraint layer explicit** -- Like CLAUDE.md, define what agents cannot do. "Do not modify files outside this directory." "Do not make network calls." "Return results in this schema."

6. **Support workflow versioning from day one** -- If workflows are durable (survive restarts), you need Temporal-style patching for in-flight changes.

---

## Sources

1. Anthropic, "Building Effective Agents" (Dec 2024) -- https://www.anthropic.com/research/building-effective-agents
2. Claude Code Documentation -- https://docs.claudecode.ai/en/overview
3. Cognition Labs, "Devin is now generally available" (Dec 2024) -- https://www.cognition.ai/blog/devin-generally-available
4. CrewAI Flows Documentation -- https://docs.crewai.com/en/concepts/flows
5. LangChain Blog, "LangGraph: Multi-Agent Workflows" -- https://blog.langchain.dev/langgraph-multi-agent-workflows/
6. Microsoft AutoGen README -- https://github.com/microsoft/autogen
7. Temporal Python SDK Documentation -- https://docs.temporal.io/develop/python/child-workflows
