# Q2: Workflow State Storage — Expert Opinion

**Date:** 2026-03-25
**Expert Role:** Database Architecture Specialist (PostgreSQL/Redis)
**Status:** Recommendation

---

## Problem

Ditto Factory needs durable workflow execution state (step status, intermediate results, resumability after crashes) for its agent orchestration engine. The system already uses Postgres/SQLite for OLTP and Redis for ephemeral payloads. The question: where does workflow state live?

## Options Evaluated

| Option | Durability | Complexity | Query Flexibility | Scale Risk |
|--------|-----------|------------|-------------------|------------|
| A. Same Postgres | High | Low | High | Medium |
| B. Dedicated store | High | High | High | Low |
| C. Event-sourced Redis | Low | Medium | Low | Low |
| D. Hybrid Postgres+Redis | High | High | High | Low |

## Analysis

### Option A: Same Postgres — RECOMMENDED for MVP

**Why it wins at this scale:**

At 10 concurrent workflows with 50 steps each, you have ~500 active step rows. This is trivially small for PostgreSQL. The "competing with OLTP" concern does not materialize until you hit thousands of concurrent workflows with sub-second polling intervals. A partial index on `status = 'running'` means your "what's executing right now?" query touches maybe 10-20 rows via index-only scan.

**Query pattern analysis:**

1. "What step is running?" -- `WHERE workflow_id = $1 AND status = 'running'` hits partial index. Cost: ~0.01ms.
2. "Show all results so far" -- `WHERE workflow_id = $1 ORDER BY step_order` hits composite index. Returns 50 rows max. Trivial.
3. "What failed?" -- `WHERE status = 'failed'` hits partial index. Even a full-table scan on 500 rows is sub-millisecond.

None of these patterns justify a separate data store.

**Durability:** Controller crash mid-workflow? The step's status is `running` in Postgres. On restart, query for `status = 'running' AND updated_at < NOW() - INTERVAL '5 minutes'` to find orphaned steps. Resume or retry. This is the simplest crash recovery model possible.

**JOINs with existing data:** Workflow steps that reference `threads`, `jobs`, and `skills` get foreign key integrity and single-query JOINs for free. With a separate store, you lose referential integrity and need application-level consistency checks.

### Why NOT the others (for now)

**Option B (Dedicated store):** Correct at 1000+ concurrent workflows. At 10, you are paying ops overhead (two backup strategies, two connection pools, two monitoring dashboards) for zero measurable benefit.

**Option C (Event-sourced Redis):** Elegant but dangerous for workflow state. Redis persistence (RDB/AOF) is not crash-safe the way Postgres WAL is. If your controller dies mid-step and Redis loses the last second of writes, you cannot reconstruct which step was running. Workflows MUST survive crashes -- this is a hard requirement, not a nice-to-have. Event sourcing also makes "show me all results so far" expensive (replay all events).

**Option D (Hybrid):** This is Option A with premature optimization. The Redis "fast reads" advantage only matters when Postgres cannot serve reads fast enough. At 500 rows with proper indexes, Postgres serves these reads in microseconds. The sync logic between Redis and Postgres is a source of bugs (what if sync fails? now your state is split). Add this layer ONLY when you have measured evidence that Postgres is the bottleneck.

### Migration path from A

When you outgrow Option A, the path is clear:

1. **First escalation (100+ concurrent workflows):** Add read replicas. Route "what's the status?" queries to replica. Zero schema changes.
2. **Second escalation (1000+ concurrent workflows):** Extract workflow tables to a dedicated Postgres instance (Option B). The schema is identical; you just change the connection string.
3. **Third escalation (need sub-10ms polling):** Add a Redis cache layer in front of Postgres for active workflow state (Option D). Postgres remains source of truth.

Each step is incremental. None require a rewrite.

## Recommendation

**Option A: Same Postgres, with well-designed indexes.**

The schema below is designed for the query patterns described, with indexes that make every common query an index scan.

## Schema Sketch

```sql
-- Workflow execution: the top-level container
CREATE TABLE workflow_executions (
    id              BIGSERIAL PRIMARY KEY,
    workflow_name   VARCHAR(255) NOT NULL,
    thread_id       BIGINT REFERENCES threads(id),
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','completed','failed','cancelled')),
    input_payload   JSONB,
    output_payload  JSONB,
    error_message   TEXT,
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Partial indexes: only index the rows you actually query
CREATE INDEX idx_wf_exec_running
    ON workflow_executions(id)
    WHERE status = 'running';

CREATE INDEX idx_wf_exec_failed
    ON workflow_executions(id, created_at DESC)
    WHERE status = 'failed';

CREATE INDEX idx_wf_exec_thread
    ON workflow_executions(thread_id)
    WHERE thread_id IS NOT NULL;


-- Workflow steps: individual units of work within an execution
CREATE TABLE workflow_steps (
    id              BIGSERIAL PRIMARY KEY,
    execution_id    BIGINT NOT NULL REFERENCES workflow_executions(id) ON DELETE CASCADE,
    step_name       VARCHAR(255) NOT NULL,
    step_order      SMALLINT NOT NULL,
    step_type       VARCHAR(50) NOT NULL,       -- 'skill_invoke', 'condition', 'parallel', 'wait'
    status          VARCHAR(20) NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending','running','completed','failed','skipped')),
    config          JSONB,                       -- step-specific configuration
    started_at      TIMESTAMPTZ,
    completed_at    TIMESTAMPTZ,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),

    UNIQUE (execution_id, step_order)
);

-- The money index: "what step is running in this workflow?"
CREATE INDEX idx_wf_steps_running
    ON workflow_steps(execution_id)
    WHERE status = 'running';

-- "Show me all steps for this workflow, in order"
CREATE INDEX idx_wf_steps_exec_order
    ON workflow_steps(execution_id, step_order);

-- Orphan detection after crash: "what's been running too long?"
CREATE INDEX idx_wf_steps_orphan
    ON workflow_steps(updated_at)
    WHERE status = 'running';


-- Step results: output data from completed steps
-- Separated from workflow_steps to keep the steps table lean for status queries
CREATE TABLE step_results (
    id              BIGSERIAL PRIMARY KEY,
    step_id         BIGINT NOT NULL UNIQUE REFERENCES workflow_steps(id) ON DELETE CASCADE,
    execution_id    BIGINT NOT NULL REFERENCES workflow_executions(id) ON DELETE CASCADE,
    result_data     JSONB NOT NULL,
    result_type     VARCHAR(50),                 -- 'success', 'partial', 'error_context'
    duration_ms     INTEGER,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- "Show me all results for this workflow"
CREATE INDEX idx_step_results_exec
    ON step_results(execution_id);


-- Helper: update timestamps automatically
CREATE OR REPLACE FUNCTION update_updated_at()
RETURNS TRIGGER AS $$
BEGIN
    NEW.updated_at = NOW();
    RETURN NEW;
END;
$$ LANGUAGE plpgsql;

CREATE TRIGGER trg_wf_exec_updated
    BEFORE UPDATE ON workflow_executions
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();

CREATE TRIGGER trg_wf_steps_updated
    BEFORE UPDATE ON workflow_steps
    FOR EACH ROW EXECUTE FUNCTION update_updated_at();
```

### Key design decisions in the schema

1. **Partial indexes everywhere.** The `WHERE status = 'running'` indexes are tiny (only active rows) and make the most common queries near-instant. As workflows complete, these indexes shrink automatically.

2. **Step results in a separate table.** `result_data` (JSONB) can be large. Keeping it out of `workflow_steps` means status-check queries on steps never load result payloads into shared buffers. The `UNIQUE` constraint on `step_id` enforces one result per step.

3. **JSONB for payloads, not TEXT.** JSONB supports indexing (`@>` operator with GIN indexes if needed later), partial extraction (`->>` operator in queries), and validation. If a specific result field becomes a frequent filter, you can add a functional index: `CREATE INDEX ON step_results((result_data->>'model_name'))`.

4. **`step_order` with UNIQUE constraint.** Prevents accidentally inserting two steps at the same position. The composite index on `(execution_id, step_order)` makes ordered retrieval an index-only scan.

5. **Orphan detection index.** After a crash, one query finds all stuck steps: `SELECT * FROM workflow_steps WHERE status = 'running' AND updated_at < NOW() - INTERVAL '5 minutes'`. The partial index makes this scan touch only running rows.

### Example queries

```sql
-- Resume after crash: find orphaned steps
SELECT ws.*, we.workflow_name
FROM workflow_steps ws
JOIN workflow_executions we ON we.id = ws.execution_id
WHERE ws.status = 'running'
  AND ws.updated_at < NOW() - INTERVAL '5 minutes';

-- Dashboard: all active workflows with current step
SELECT
    we.id,
    we.workflow_name,
    we.status,
    ws.step_name AS current_step,
    ws.step_order,
    we.started_at,
    NOW() - we.started_at AS elapsed
FROM workflow_executions we
LEFT JOIN workflow_steps ws
    ON ws.execution_id = we.id AND ws.status = 'running'
WHERE we.status = 'running';

-- Get full workflow history with results
SELECT
    ws.step_name,
    ws.step_order,
    ws.status,
    ws.duration_ms,
    sr.result_data,
    ws.started_at,
    ws.completed_at
FROM workflow_steps ws
LEFT JOIN step_results sr ON sr.step_id = ws.id
WHERE ws.execution_id = $1
ORDER BY ws.step_order;
```

### Connection pooling note

Since this shares the Postgres instance, ensure you are using PgBouncer or Supabase's built-in pooler in **transaction mode**. At 10 concurrent workflows, even without pooling you would be fine, but transaction-mode pooling prevents connection exhaustion if the workflow engine scales up. Do NOT use session-mode pooling -- it defeats the purpose for short-lived queries like status checks.

---

## TL;DR

Use the same Postgres. Add three tables with partial indexes. The scale does not justify a separate store. When it does, extract the tables to a dedicated instance -- the schema migrates unchanged.
