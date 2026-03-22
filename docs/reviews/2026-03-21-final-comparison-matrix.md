# Final Comparison Matrix: Traceability Approaches

**Date:** 2026-03-21 | **Reviewers:** PM, Trend Researcher, Software Architect, DevOps Engineer

---

## Unanimous Verdict: Approach B Wins, With a Roadmap

All four reviewers independently recommend **Approach B (Structured Logs + SQLite)** as the starting point. The disagreement is only about *what comes next*.

---

## Comparison Matrix

| Dimension | A: OTel-Native | B: Structured Logs | C: Event Sourcing | D: Langfuse |
|-----------|---------------|--------------------|--------------------|-------------|
| **PM Score** | 5.8/10 | **8.7/10** | 5.8/10 | 6.8/10 |
| **Effort** | 14-19 days | **7-9 days** | 12-16 days | 10-13 days |
| **New Infra** | Collector + Tempo/Jaeger | **None** | Materializer worker | Docker Compose (1.5GB) |
| **Ops Burden** | 15-25% FTE | **1-2% FTE** | 5-10% FTE | 10-20% FTE |
| **Cost (50 runs/day)** | ~$50-100/mo | **~$0** | ~$10-20/mo | ~$30-60/mo |
| **Architectural Fit** | Good but heavy | **Best — follows PerformanceTracker pattern** | Clever but over-engineered | Good but adds 3 services |
| **Cross-Process** | W3C traceparent in Redis | trace_id in Redis payload | Natural (shared Redis) | Langfuse trace_id in Redis |
| **Agent-Side Tracing** | Weak (no bash SDK) | **redis-cli compatible** | redis-cli compatible | Weak (Python SDK, agent is Node) |
| **Failure Blast Radius** | Medium (collector outage) | **Lowest (ACID SQLite)** | High (shared Redis memory) | Medium (Langfuse outage) |
| **Query Flexibility** | TraceQL (powerful) | SQL (good enough) | SQL on materialized views | Langfuse API (limited) |
| **Visualization** | Grafana/Tempo UI | **Custom Markdown reports** | Custom Markdown reports | Langfuse UI (beautiful) |
| **Migration Path** | Terminal | From B (mechanical) | Unnecessary | From B (2-5 days) |

---

## The Decisive Constraint (from Architect)

> **The agent Docker image is `node:22-slim` with no Python runtime.** This eliminates Langfuse (Python SDK) and weakens OTel (no bash SDK) for agent-side instrumentation. Only B and C can instrument via `redis-cli`.

> **`claude -p` is a black box.** No approach can see inside it. Agent-side tracing is limited to pre/post execution events regardless of approach.

---

## What Each Reviewer Said

### Product Manager
- "Engineers need a **review tool**, not an SRE observability platform"
- B scores 8.7 because it matches team size, stage, and actual user need
- Steal formal event schema from C and LLM cost fields from D
- Documented explicit triggers for when to revisit (3+ services → OTel, high LLM cost → Langfuse)

### Trend Researcher
- Langfuse dominates OSS LLM observability (19K stars, acquired by ClickHouse Jan 2026)
- OTel GenAI agent conventions still experimental (not stable)
- Market converging on layered architecture: OTel transport + purpose-built analysis
- **Migration cost B → D is low (2-5 days)** if schema uses W3C trace IDs and Langfuse-compatible observation types
- Recommendation: B now → D at month 3-6 → A as long-term option

### Software Architect
- B follows the existing `PerformanceTracker` pattern — lowest friction
- Event sourcing (C) adds unnecessary CQRS complexity
- Langfuse (D) adds 3 services the team doesn't need yet
- **If B's schema matches OTel span model** (`trace_id`, `span_id`, `parent_span_id`), migration to A is mechanical
- All approaches share the same blind spot: can't see inside `claude -p`

### DevOps Engineer
- B: zero new infra, ~$0 cost, 1-2% DevOps time — "wins decisively"
- C is risky: shares Redis memory with the critical agent communication bus
- D exceeds the 10% FTE budget for a small team
- A is the right Phase 2 when scale justifies it
- Use OTel semantic conventions in B's schema for forward compatibility

---

## Your Options

### Option 1: Pure B (Fastest, Simplest)
**Structured Logs + SQLite. Ship in 7-9 days.**

- Zero dependencies, follows existing patterns
- Custom Markdown reports for engineer review
- SQLite trace_events.db alongside existing skill registry
- Risk: may need to rewrite if you outgrow it

### Option 2: B + D Roadmap (PM + Trend Researcher recommendation)
**Start with B, migrate to Langfuse at month 3-6.**

- Design B's schema with Langfuse-compatible fields (W3C trace IDs, observation types)
- Get traces working in 7-9 days
- Add Langfuse when you need the UI, cost tracking, or prompt management
- Migration cost: 2-5 days when ready
- Risk: slightly more upfront schema design

### Option 3: B + A Roadmap (Architect + DevOps recommendation)
**Start with B using OTel conventions, migrate to full OTel when needed.**

- B's schema uses `trace_id`, `span_id`, `parent_span_id` matching OTel span model
- When you hit 3+ services or need distributed tracing, swap to OTel backend
- Migration is mechanical (schema already matches)
- Risk: OTel GenAI conventions may change (still experimental)

### Option 4: B Enhanced (PM's hybrid)
**B + best ideas stolen from C and D.**

- Formal event types from C (TaskReceived, SkillsClassified, ToolInvoked, etc.)
- LLM cost/token fields from D
- OTel-compatible span IDs from A
- Still zero infra, still SQLite, still 7-9 days (maybe +2 days for schema)
- Best of all worlds at current scale

### Option 5: Skip B, Go Straight to D (Contrarian)
**If you believe you'll need Langfuse within 3 months anyway, skip the intermediate step.**

- 10-13 days to ship
- Beautiful trace UI immediately
- Cost tracking, prompt management, evaluations built in
- Risk: agent-side instrumentation is weak (Node image, no Python SDK), ops burden higher

---

## Documents Produced

### Architecture Plans
| File | Approach |
|------|----------|
| `docs/plans/tracing-approach-a-otel-native.md` | OpenTelemetry-Native |
| `docs/plans/tracing-approach-b-structured-logs.md` | Structured Logs + SQLite |
| `docs/plans/tracing-approach-c-event-sourcing.md` | Event Sourcing + Redis Streams |
| `docs/plans/tracing-approach-d-langfuse.md` | Langfuse-Integrated |

### Cross-Examination Reviews
| File | Reviewer |
|------|----------|
| `docs/reviews/2026-03-21-pm-cross-examination.md` | Product Manager |
| `docs/reviews/2026-03-21-trend-analysis-llm-observability.md` | Trend Researcher |
| `docs/reviews/2026-03-21-architect-cross-examination.md` | Software Architect |
| `docs/reviews/2026-03-21-devops-ops-assessment.md` | DevOps Engineer |
| `docs/reviews/2026-03-21-traceability-analysis.md` | Initial Analysis |
| `docs/reviews/2026-03-21-final-comparison-matrix.md` | This document |
