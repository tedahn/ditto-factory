# PM Cross-Examination: Traceability & Reporting Architecture

**Author**: Alex (Product Manager)
**Date**: 2026-03-21
**Status**: Recommendation Ready
**Decision needed by**: 2026-03-28

---

## 0. Framing: What Problem Are We Actually Solving?

Before evaluating architectures, let me state the user problem clearly:

> Engineers reviewing agent work need to answer three questions fast:
> 1. **What did the agent do?** (timeline of actions, tool calls, files touched)
> 2. **Why did it do that?** (which skills were injected, what classification drove the decision, what context the LLM had)
> 3. **Was the outcome good?** (did the code compile, did tests pass, did it match the task intent)

That is the job-to-be-done. Everything else -- flame graphs, distributed tracing, event replay -- is implementation detail until we validate that engineers actually need it.

Secondary need (product-level, not user-level): Ditto Factory's "Open Agent Harness" philosophy demands that **every outcome is traceable and every deployment is reproducible**. This means traces must be durable, queryable, and exportable -- not just viewable in a transient UI.

---

## 1. User Needs Analysis

| Need | Priority | Notes |
|------|----------|-------|
| "Show me what the agent did, step by step" | P0 | Every engineer needs this on every task |
| "Show me which skills were selected and why" | P0 | Critical for debugging skill hotloading -- our current active development area |
| "Generate a Markdown report I can paste into a PR or Slack" | P0 | This is how results flow back to the requesting engineer |
| "Show me token/cost breakdown per task" | P1 | Important for cost management as usage scales |
| "Let me search across historical traces" | P1 | Pattern recognition: "which skills keep failing?" |
| "Real-time streaming of agent progress" | P2 | Nice to have; engineers currently wait for completion |
| "Distributed flame graph across services" | P3 | We have ONE service. This is a solution looking for a problem we don't have. |

**Key insight**: Our users are engineers reviewing completed agent work, not SREs debugging production latency. The traceability system is a **review tool**, not an **observability tool**. This distinction matters enormously for architecture selection.

---

## 2. Approach-by-Approach Cross-Examination

### Approach A: OpenTelemetry-Native

**The hard questions:**

- **Who is this for?** OTel is designed for distributed systems observability -- correlating requests across microservices. Ditto Factory is a single FastAPI service that spawns K8s pods. We don't have a distributed tracing problem. We have a "what did this one agent do?" problem.
- **What's the real ops cost?** OTel Collector + Jaeger/Tempo + persistent storage. That's 2-3 new services to deploy, monitor, and maintain. For a small team in early stage, that is not a "set it and forget it" addition -- it's ongoing operational burden.
- **Are GenAI semantic conventions stable?** No. They're in "Development" status per the OTel spec. Building on unstable conventions means breaking changes we'll have to absorb.
- **What's the time-to-first-useful-trace?** Realistically 2-3 weeks including infrastructure setup, SDK integration, custom span instrumentation, and backend configuration.

**Verdict**: Over-engineered for our current reality. This is a bet on a future we haven't earned yet. If we grow to 5+ services with complex inter-service communication, revisit. Today, this is resume-driven architecture.

**Risk profile**:
| Risk | Likelihood | Impact |
|------|-----------|--------|
| Infrastructure complexity slows team | High | High |
| GenAI conventions change, breaking our spans | Medium | Medium |
| Team spends more time debugging OTel than the product | Medium | High |
| Over-investment delays skill hotloading work | High | High |

---

### Approach B: Lightweight Structured Logs + SQLite

**The hard questions:**

- **Does this actually solve the P0 needs?** Yes. JSON log events with trace_id correlation, stored in SQLite, queryable via SQL, rendered to Markdown. That is exactly the "what did the agent do?" answer our engineers need.
- **What about scale?** SQLite handles millions of rows without breaking a sweat for read-heavy workloads. Our current scale is dozens of agent runs per day. SQLite will be fine for 12+ months.
- **What do we lose?** No pretty trace UI. No flame graphs. No real-time streaming. But our users aren't asking for those things -- they're asking for reports they can paste into PRs.
- **What's the migration cost when we outgrow it?** Low. Structured JSON events with consistent schemas can be replayed into any future system. The data format is the API; the storage is an implementation detail.
- **Time-to-first-useful-trace?** 3-5 days. Maybe less. This is Python logging + SQLite. Every engineer on the team already knows how to debug this.

**Verdict**: This is the right tool for the right stage. It solves the P0 problem with near-zero operational overhead. The lack of fancy visualization is a feature, not a bug -- it forces us to think about what data actually matters before we invest in presentation.

**Risk profile**:
| Risk | Likelihood | Impact |
|------|-----------|--------|
| Outgrow SQLite at scale | Low (12+ months away) | Medium (migration is manageable) |
| Manual correlation logic has bugs | Medium | Low (simple to debug) |
| No real-time visibility during long agent runs | Medium | Low (P2 need) |
| Engineers want richer visualization | Low (near-term) | Low (can layer on later) |

---

### Approach C: Event Sourcing with Redis Streams

**The hard questions:**

- **Is event sourcing solving a problem we have?** The replay capability is intellectually appealing. But when has anyone on the team said "I wish I could replay this agent's execution from event 47"? Never. We need traces, not event replay.
- **What's the real complexity cost?** CQRS means two data models (write events + read projections), a materializer process, eventual consistency semantics, and Redis memory management. For a team that's actively building skill hotloading, this is a massive context switch.
- **Redis memory pressure is real.** We already use Redis for task payloads and skill injection. Adding unbounded event streams to the same Redis instance is a memory pressure risk that requires active management (TTLs, eviction policies, monitoring).
- **What happens when the materializer falls behind?** Stale read models. Engineers query for a trace and get incomplete data. Now you're debugging the traceability system instead of the agent.
- **Time-to-first-useful-trace?** 2-3 weeks minimum, and that's optimistic. CQRS done poorly is worse than no CQRS.

**Verdict**: Architecturally elegant, practically premature. This is the kind of system you build when you've validated your data model with something simpler and know exactly what temporal queries you need. We haven't done that work yet.

**Risk profile**:
| Risk | Likelihood | Impact |
|------|-----------|--------|
| CQRS complexity exceeds team capacity | High | High |
| Redis memory pressure affects core task processing | Medium | High |
| Materializer bugs produce stale/wrong trace data | Medium | High |
| Time investment delays skill hotloading | High | High |

---

### Approach D: Langfuse-Integrated

**The hard questions:**

- **Does Langfuse solve our specific problem?** Partially. It's excellent for LLM call tracing -- token counts, latencies, prompt/completion pairs. But our traceability need is broader: we need to trace the full lifecycle from webhook receipt -> classification -> skill selection -> agent spawn -> code execution -> result. Langfuse sees the LLM calls but not the orchestration around them.
- **What's the operational cost?** Self-hosted Langfuse means deploying and maintaining another service (Postgres + Langfuse server). It's not zero-ops. It's also not our service -- we inherit their bugs, their upgrade cycle, their breaking changes.
- **Cross-process tracing?** Our agents run in separate K8s pods. Langfuse requires manual trace ID passing across process boundaries. This works but it's fragile -- if the agent pod doesn't propagate the trace ID correctly, we lose the connection.
- **Can we generate our custom Markdown reports from Langfuse data?** Yes, via their API. But we're constrained by their data model. If we need a report format that doesn't map cleanly to Langfuse's trace/span/generation hierarchy, we're writing adapter code.
- **Vendor dependency risk?** Langfuse is open-source but VC-funded. Their priorities may diverge from ours. Self-hosting mitigates this but doesn't eliminate it -- we still depend on their SDK and data model.
- **Time-to-first-useful-trace?** 1-2 weeks. The decorator-based instrumentation is genuinely fast to integrate for LLM calls. But full lifecycle tracing (orchestration + agent) takes longer.

**Verdict**: Tempting because it's batteries-included, but it solves the LLM observability problem better than it solves our full-lifecycle traceability problem. If our only need were "what did the LLM do?", this would be the clear winner. But we need "what did the entire system do from webhook to PR comment?", and Langfuse only covers part of that story.

**Risk profile**:
| Risk | Likelihood | Impact |
|------|-----------|--------|
| Partial coverage of our traceability needs | High | Medium |
| Self-hosted ops burden | Medium | Medium |
| SDK/API breaking changes on upgrade | Low | Medium |
| Data model mismatch for custom reports | Medium | Medium |
| Vendor priority divergence | Low | High (long-term) |

---

## 3. Decision Matrix

| Criterion (Weight) | A: OTel | B: Logs+SQLite | C: Event Sourcing | D: Langfuse |
|---------------------|---------|----------------|-------------------|-------------|
| Solves P0 user needs (30%) | 7/10 | 9/10 | 7/10 | 7/10 |
| Time-to-value (25%) | 3/10 | 9/10 | 4/10 | 7/10 |
| Ops burden for small team (20%) | 3/10 | 10/10 | 4/10 | 6/10 |
| Supports "traceable + reproducible" vision (15%) | 8/10 | 7/10 | 9/10 | 7/10 |
| Migration path / future flexibility (10%) | 9/10 | 7/10 | 6/10 | 5/10 |
| **Weighted Score** | **5.8** | **8.7** | **5.8** | **6.8** |

---

## 4. Recommendation

### Primary: Approach B (Structured Logs + SQLite) -- with two targeted enhancements

**Confidence level: 80%**

**Rationale:**

Approach B wins because it matches our current reality: small team, early-stage product, active skill-hotloading development underway. Every hour spent on traceability infrastructure is an hour not spent on the features that will determine whether Ditto Factory succeeds. Approach B minimizes that trade-off.

The "traceable, reproducible" philosophy doesn't require complex infrastructure. It requires **structured, durable, queryable data**. JSON events in SQLite are all three.

### Two targeted enhancements to Approach B:

**Enhancement 1: Steal the event schema discipline from Approach C.**

Don't just log ad-hoc JSON. Define a formal event schema upfront:

```
TaskReceived -> TaskClassified -> SkillsSelected -> AgentSpawned ->
ToolExecuted -> LLMCalled -> AgentCompleted -> ReportGenerated
```

Each event type has a fixed schema with required fields. This gives us the replay capability of event sourcing without the CQRS complexity. If we ever need to migrate to Redis Streams or OTel, having disciplined events makes that migration trivial.

**Enhancement 2: Steal the LLM-specific fields from Approach D.**

For `LLMCalled` events, capture: model, token counts (input/output), latency, estimated cost, and prompt hash. This is 20 lines of code -- not a Langfuse deployment. It gives us the cost visibility we'll need at P1 priority without taking on a dependency.

### What would change this recommendation:

| Trigger | New recommendation |
|---------|-------------------|
| We grow to 3+ services with complex inter-service calls | Re-evaluate Approach A (OTel) |
| LLM cost exceeds $X/month and we need deep prompt-level optimization | Add Langfuse for LLM-specific observability alongside B |
| We need real-time trace streaming for long-running agents | Layer WebSocket streaming on top of B's event model |
| Team grows to 5+ engineers wanting self-serve trace exploration | Add a lightweight trace UI (could be as simple as a Datasette instance on the SQLite DB) |

### Explicit non-goals for v1:

- No distributed tracing (we have one service)
- No real-time streaming UI (P2, revisit in Q3)
- No flame graphs (we're not debugging latency)
- No event replay/rebuild (intellectually cool, practically unnecessary today)

---

## 5. Proposed Timeline

| Week | Deliverable | Owner |
|------|-------------|-------|
| 1 | Event schema definition (all event types + required fields) | PM + Eng Lead |
| 1 | SQLite handler + JSON event writer | Eng |
| 2 | Instrument orchestrator: TaskReceived through AgentSpawned | Eng |
| 2 | Instrument agent: ToolExecuted, LLMCalled, AgentCompleted | Eng |
| 3 | Markdown report generator (SQL queries -> report template) | Eng |
| 3 | Wire reports into Slack/GitHub webhook responses | Eng |
| 4 | LLM cost tracking fields + basic cost dashboard | Eng |
| 4 | Search API for historical traces | Eng |

**Total: 4 weeks to full lifecycle traceability with exportable reports.**

Compare: Approach A would need 2-3 weeks just for infrastructure before writing a single line of instrumentation code.

---

## 6. Success Metrics

| Metric | Target | Measurement Window |
|--------|--------|--------------------|
| % of agent runs with complete trace (all event types present) | >= 95% | 30 days post-launch |
| Time for engineer to answer "what did the agent do?" | < 30 seconds (via report) | 30 days post-launch |
| Report generation latency | < 2 seconds | At launch |
| Traceability system uptime (no data loss) | 99.9% | 90 days post-launch |
| Engineer satisfaction with trace completeness | >= 4/5 survey | 60 days post-launch |

---

## 7. Open Questions

- [ ] **Event retention policy**: How long do we keep trace data? 30 days? 90 days? Indefinite? -- Owner: PM -- Deadline: 2026-03-28
- [ ] **SQLite location for agent pods**: Agent pods are ephemeral. Do we write events to a mounted volume and collect them, or stream events back to the controller via Redis? -- Owner: Eng Lead -- Deadline: 2026-03-28
- [ ] **Report format**: Do we need different report formats for Slack (compact) vs GitHub PR (detailed) vs Linear (structured)? -- Owner: PM + Design -- Deadline: 2026-04-04

---

*"The best traceability system is the one that ships next week and actually gets used, not the one that's architecturally perfect and ships next quarter."*
