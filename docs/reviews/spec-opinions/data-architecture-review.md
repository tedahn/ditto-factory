# Data Architecture Review: Workflow Engine Spec

**Reviewer:** Database Architect Agent
**Date:** 2026-03-25
**Spec:** `docs/superpowers/specs/2026-03-25-workflow-engine-design.md`
**Existing schema:** `controller/migrations/002_skill_registry.sql`

---

## 1. Schema Review (Column-by-Column)

### workflow_templates

| Column | Type | Verdict | Notes |
|:-------|:-----|:--------|:------|
| id | TEXT PK | OK | Consistent with existing `skills.id` pattern (TEXT UUIDs) |
| slug | TEXT UNIQUE NOT NULL | OK | Has partial index (`WHERE is_active = true`) |
| name | TEXT NOT NULL | OK | |
| description | TEXT | OK | Nullable is fine |
| version | INTEGER DEFAULT 1 | CONCERN | No composite unique on `(slug, version)`. If slug is unique, old versions are lost on update. Need a `workflow_template_versions` table (like `skill_versions`) or change the unique constraint to `(slug, version)` and remove UNIQUE from slug alone |
| definition | JSONB NOT NULL | SEE SECTION 3 | |
| parameter_schema | JSONB | OK | JSON Schema validation metadata, appropriate for JSONB |
| is_active | BOOLEAN DEFAULT true | OK | Soft delete pattern matches existing `skills.is_active` |
| created_by | TEXT NOT NULL | OK | |
| created_at | TIMESTAMPTZ | INCONSISTENCY | Existing schema uses `TEXT DEFAULT (datetime('now'))` for SQLite. This uses `TIMESTAMPTZ DEFAULT now()` -- Postgres only |
| updated_at | TIMESTAMPTZ | SAME ISSUE | |

### workflow_executions

| Column | Type | Verdict | Notes |
|:-------|:-----|:--------|:------|
| id | TEXT PK | OK | |
| template_id | TEXT FK | OK | References workflow_templates(id) |
| template_version | INTEGER | OK | Snapshot of version at execution time -- good pattern |
| thread_id | TEXT NOT NULL | MISSING FK | Should reference `threads(id)` if threads table exists. Also: no cascading behavior defined |
| parameters | JSONB NOT NULL | OK | Runtime-resolved values, JSONB is correct |
| status | TEXT NOT NULL | CONCERN | No CHECK constraint. Should be `CHECK (status IN ('pending','running','completed','failed','cancelled'))` |
| started_at | TIMESTAMPTZ | OK | |
| completed_at | TIMESTAMPTZ | OK | |
| result | JSONB | OK | Final aggregated output |
| error | TEXT | OK | |
| created_at | TIMESTAMPTZ | OK | |

**Missing columns:**
- `cancelled_by TEXT` -- who cancelled it? (for audit trail)
- `duration_ms INTEGER` -- computed on completion, useful for monitoring without date math

### workflow_steps

| Column | Type | Verdict | Notes |
|:-------|:-----|:--------|:------|
| id | TEXT PK | OK | |
| execution_id | TEXT FK | OK | ON DELETE CASCADE -- good |
| step_id | TEXT NOT NULL | CONCERN | This is the template-defined step name (e.g., "search"). Should have UNIQUE(execution_id, step_id) to prevent duplicate steps within an execution |
| step_type | TEXT NOT NULL | OK | Could benefit from CHECK constraint |
| depends_on | TEXT[] DEFAULT '{}' | PROBLEM | Array type -- see SQLite section |
| status | TEXT NOT NULL | OK | Needs CHECK constraint like executions |
| input | JSONB | OK | |
| output | JSONB | OK | |
| agent_jobs | TEXT[] DEFAULT '{}' | PROBLEM | Array type -- see SQLite section |
| started_at | TIMESTAMPTZ | OK | |
| completed_at | TIMESTAMPTZ | OK | |
| error | TEXT | OK | |
| retry_count | INTEGER DEFAULT 0 | OK | |
| max_retries | INTEGER DEFAULT 2 | OK | |
| created_at | TIMESTAMPTZ | OK | |

**Missing columns:**
- `timeout_seconds INTEGER` -- the per-step timeout, needed for the engine to enforce deadlines
- `on_failure TEXT DEFAULT 'fail_workflow'` -- stored per-step so the engine does not need to re-parse the template definition during execution

### workflow_step_agents

| Column | Type | Verdict | Notes |
|:-------|:-----|:--------|:------|
| id | TEXT PK | OK | |
| step_id | TEXT FK | OK | ON DELETE CASCADE -- good |
| agent_index | INTEGER NOT NULL | OK | Should have UNIQUE(step_id, agent_index) |
| k8s_job_name | TEXT | OK | Nullable for pre-spawn state |
| thread_id | TEXT NOT NULL | OK | |
| status | TEXT DEFAULT 'pending' | OK | Needs CHECK constraint |
| input | JSONB | OK | |
| output | JSONB | OK | |
| started_at | TIMESTAMPTZ | OK | |
| completed_at | TIMESTAMPTZ | OK | |
| error | TEXT | OK | |

**Missing columns:**
- `created_at TIMESTAMPTZ` -- every table should have this for debugging
- `exit_code INTEGER` -- agent exit code, useful for distinguishing crash vs. logic failure

### ALTER TABLE jobs (existing table)

| Change | Verdict | Notes |
|:-------|:--------|:------|
| ADD workflow_execution_id TEXT FK | OK | Nullable is correct |
| ADD workflow_step_id TEXT FK | CONCERN | Should also add indexes on both columns for join performance |

---

## 2. Index Analysis

### Current indexes and query plan assessment

| Index | Target Query | Verdict |
|:------|:-------------|:--------|
| `idx_wf_tmpl_slug` (partial, `WHERE is_active`) | Template lookup by slug | GOOD -- partial index keeps it small, covers the common case |
| `idx_wf_exec_active` (partial, `WHERE status IN (...)`) | Poll for active executions | GOOD -- partial index on hot data only |
| `idx_wf_exec_thread` | Find executions for a thread | GOOD |
| `idx_wf_steps_exec` | Get all steps for an execution | GOOD -- primary query pattern |
| `idx_wf_steps_active` (partial) | Find runnable steps | GOOD |
| `idx_wf_step_agents_step` | Get all agents for a step | GOOD |

### Missing indexes

| Missing Index | Required For | Priority |
|:--------------|:-------------|:---------|
| `idx_wf_exec_template` ON workflow_executions(template_id) | FK join, "find executions by template" | HIGH -- every FK should have an index |
| `idx_wf_step_agents_status` ON workflow_step_agents(step_id, status) | "Are all agents done?" check in `handle_agent_result` | MEDIUM -- composite covers the hot query |
| `idx_jobs_workflow_exec` ON jobs(workflow_execution_id) WHERE workflow_execution_id IS NOT NULL | "Find jobs for an execution" | HIGH -- new FK columns need indexes |
| `idx_jobs_workflow_step` ON jobs(workflow_step_id) WHERE workflow_step_id IS NOT NULL | "Find jobs for a step" | HIGH |

### Over-indexing assessment

No over-indexing detected. The partial index strategy is correct. One minor note: `idx_wf_tmpl_slug` is redundant with the UNIQUE constraint on `slug` (which creates an implicit unique index). The partial index only helps if you query with `WHERE is_active = true` -- confirm the application always includes this filter.

### Query plan analysis: "Get execution with all steps"

```sql
-- Expected query:
SELECT e.*, s.*
FROM workflow_executions e
JOIN workflow_steps s ON s.execution_id = e.id
WHERE e.id = $1;
```

**Plan:** PK lookup on `workflow_executions` (Index Scan) + Index Scan on `idx_wf_steps_exec`. This is optimal. No concerns.

### Query plan analysis: "Find orphaned steps"

```sql
-- Expected query:
SELECT s.*
FROM workflow_steps s
JOIN workflow_executions e ON e.id = s.execution_id
WHERE e.status IN ('failed', 'cancelled')
  AND s.status = 'running';
```

**Plan:** The partial index `idx_wf_steps_active` covers `status IN ('pending', 'running')` which helps filter steps. The join to executions uses `idx_wf_steps_exec`. This should perform well. For a monitoring dashboard that runs this query frequently, consider a composite index: `ON workflow_steps(status, execution_id)`.

---

## 3. JSONB Usage Assessment

| Column | Current: JSONB | Should Be Relational? | Verdict |
|:-------|:---------------|:----------------------|:--------|
| `workflow_templates.definition` | Full template JSON with steps, agent specs, etc. | NO | Correct as JSONB. This is a document that is read as a whole and versioned atomically. Normalizing steps into rows would create a painful template CRUD experience. |
| `workflow_templates.parameter_schema` | JSON Schema definition | NO | Correct. This is a validation schema, not queryable data. |
| `workflow_executions.parameters` | Resolved runtime parameters | NO | Correct. Key-value bag that varies per template. |
| `workflow_executions.result` | Final aggregated output | NO | Correct. Arbitrary structure defined by template. |
| `workflow_steps.input` | Step input data | NO | Correct. Varies by step type. |
| `workflow_steps.output` | Step output data | MAYBE | If you need to query across step outputs (e.g., "find all executions that returned > 10 events"), consider a GIN index: `CREATE INDEX idx_wf_steps_output_gin ON workflow_steps USING GIN (output jsonb_path_ops)`. But only add this if you have the query pattern. |
| `workflow_step_agents.input` | Per-agent input | NO | Correct. |
| `workflow_step_agents.output` | Per-agent result | NO | Correct. |

**Overall:** JSONB usage is appropriate. The data model correctly separates structural metadata (relational columns with indexes) from payload data (JSONB blobs). No normalization changes recommended.

---

## 4. SQLite Compatibility Matrix

The existing `002_skill_registry.sql` uses SQLite-native patterns. The new spec uses Postgres-native patterns. These must be reconciled.

| Feature | Spec Uses | SQLite Equivalent | Migration Effort |
|:--------|:----------|:------------------|:-----------------|
| `JSONB` | `definition JSONB`, `parameters JSONB`, etc. | `TEXT` with `json()` / `json_extract()` | MEDIUM -- replace all JSONB with TEXT, use `json_extract()` for queries. SQLite 3.38+ has `->` and `->>` operators. |
| `TEXT[]` (arrays) | `depends_on TEXT[]`, `agent_jobs TEXT[]` | `TEXT` as JSON array (like existing `skills.language`) | LOW -- store as JSON text `'["step1","step2"]'`, parse in application |
| `TIMESTAMPTZ` | All timestamp columns | `TEXT DEFAULT (datetime('now'))` | LOW -- match existing pattern from `002_skill_registry.sql` |
| `DEFAULT now()` | All `created_at`/`updated_at` | `DEFAULT (datetime('now'))` | LOW |
| `DEFAULT true` / `DEFAULT false` | `is_active` | `DEFAULT 1` / `DEFAULT 0` | LOW -- match existing `skills.is_active` pattern |
| Partial indexes (`WHERE ...`) | `idx_wf_tmpl_slug`, `idx_wf_exec_active`, `idx_wf_steps_active` | SQLite supports partial indexes (3.8.0+) | NONE -- compatible |
| `IN (...)` in partial index | `WHERE status IN ('pending', 'running')` | SQLite supports this | NONE -- compatible |
| `GIN` index (if added) | Potential JSONB index | Not available in SQLite | N/A -- skip in SQLite, only create in Postgres |
| `ON DELETE CASCADE` | FK constraints | Supported but requires `PRAGMA foreign_keys = ON` | LOW -- ensure pragma is set at connection time |
| `CHECK` constraints | Recommended additions | Fully supported in SQLite | NONE |

**Recommendation:** Write a single migration file with SQLite syntax (matching the existing `002_skill_registry.sql` pattern). Add a Postgres-specific overlay migration that adds JSONB casts, GIN indexes, and other Postgres features. This is the same pattern used by Django and similar ORMs.

---

## 5. Migration Safety

### ALTER TABLE on existing `jobs` table

```sql
ALTER TABLE jobs ADD COLUMN workflow_execution_id TEXT REFERENCES workflow_executions(id);
ALTER TABLE jobs ADD COLUMN workflow_step_id TEXT REFERENCES workflow_steps(id);
```

| Concern | Risk | Mitigation |
|:--------|:-----|:-----------|
| Table lock during ALTER | LOW (Postgres 11+) | Adding a nullable column with no default does NOT rewrite the table. This is a metadata-only change. Safe for production. |
| SQLite ALTER TABLE | LOW | SQLite supports `ADD COLUMN` for nullable columns without defaults. Safe. |
| FK constraint on existing rows | NONE | New columns are nullable, existing rows get NULL, which satisfies the FK constraint. |
| Missing indexes on new FK columns | HIGH | The spec does not add indexes for `workflow_execution_id` and `workflow_step_id` on the `jobs` table. These MUST be added. Without them, joins from executions/steps to jobs will do full table scans. |

**Required additions:**
```sql
CREATE INDEX idx_jobs_wf_exec ON jobs(workflow_execution_id) WHERE workflow_execution_id IS NOT NULL;
CREATE INDEX idx_jobs_wf_step ON jobs(workflow_step_id) WHERE workflow_step_id IS NOT NULL;
```

For Postgres production, use `CREATE INDEX CONCURRENTLY` to avoid table locks.

### New table creation

All new tables use `CREATE TABLE` (not `ALTER TABLE`). No risk to existing data. These are purely additive.

### Reversibility

**Missing:** The spec provides no DOWN migration. Every migration should include:

```sql
-- DOWN
DROP INDEX IF EXISTS idx_jobs_wf_step;
DROP INDEX IF EXISTS idx_jobs_wf_exec;
ALTER TABLE jobs DROP COLUMN IF EXISTS workflow_step_id;
ALTER TABLE jobs DROP COLUMN IF EXISTS workflow_execution_id;
DROP TABLE IF EXISTS workflow_step_agents;
DROP TABLE IF EXISTS workflow_steps;
DROP TABLE IF EXISTS workflow_executions;
DROP TABLE IF EXISTS workflow_templates;
```

Note: SQLite does not support `DROP COLUMN` (prior to 3.35.0). For older SQLite versions, the DOWN migration requires table recreation.

---

## 6. Growth Patterns and Retention

### Storage projection (100 workflows/day, 10 steps each)

| Table | Rows/Day | Row Size (est.) | Daily Growth | 1-Year Growth |
|:------|:---------|:-----------------|:-------------|:--------------|
| workflow_templates | ~0 (CRUD, not per-execution) | ~2 KB | negligible | negligible |
| workflow_executions | 100 | ~500 B (metadata) + ~5 KB (result JSONB) | ~550 KB | ~200 MB |
| workflow_steps | 1,000 | ~200 B (metadata) + ~10 KB (input+output JSONB) | ~10 MB | ~3.6 GB |
| workflow_step_agents | 5,000 (avg 5 agents/step for fan-outs) | ~200 B + ~5 KB (output JSONB) | ~26 MB | ~9.5 GB |
| **Total** | | | **~37 MB/day** | **~13.3 GB/year** |

### Retention concerns

- **workflow_step_agents** is the largest table. At 5,000 rows/day, it hits 1.8M rows/year. The JSONB `output` column dominates storage.
- **Index bloat:** Partial indexes help, but `idx_wf_step_agents_step` covers all rows. At 1.8M rows, this index is ~50 MB. Not alarming.

### Recommended retention strategy

```sql
-- Archive completed workflows older than 90 days
-- Move to workflow_executions_archive / workflow_steps_archive tables
-- Or simply delete (if results are already delivered):

DELETE FROM workflow_executions
WHERE status IN ('completed', 'failed', 'cancelled')
  AND completed_at < NOW() - INTERVAL '90 days';

-- CASCADE will clean up workflow_steps and workflow_step_agents
```

**Missing from spec:** No retention policy is defined. Add:
1. A `retention_days` setting (default 90)
2. A periodic cleanup job (cron or async worker)
3. Consider partitioning `workflow_steps` by `created_at` if growth exceeds projections (Postgres 10+ declarative partitioning)

---

## 7. Race Condition Analysis

### The critical path: concurrent `advance()` calls

The `advance()` method is called after every step completion. With fan-out steps, multiple agents can complete simultaneously, each triggering `advance()`. This creates a race condition.

**Scenario:**

```
Step A (fan-out, 3 agents) -> Step B (aggregate) -> Step C (report)

Timeline:
  Agent 0 completes -> handle_agent_result -> all done? YES -> complete_step A -> advance()
  Agent 1 completes -> handle_agent_result -> all done? YES -> complete_step A -> advance()
  Agent 2 completes -> handle_agent_result -> all done? YES -> complete_step A -> advance()
```

If agents 0, 1, and 2 complete near-simultaneously:

1. **Race in `handle_agent_result`:** Two agents check "all done?" simultaneously. Both see 2 of 3 completed (the third hasn't been committed yet). Neither triggers advance. **Result: workflow hangs forever.**

2. **Race in `advance`:** If two agents both trigger advance, Step B could be started twice. **Result: duplicate work, corrupted results.**

### Required fix: Row-level locking

```sql
-- In handle_agent_result: lock the step row
SELECT * FROM workflow_steps WHERE id = $1 FOR UPDATE;

-- Then atomically check all agents and update step status
UPDATE workflow_step_agents SET status = 'completed', output = $2 WHERE id = $3;

-- Re-check within the same transaction
SELECT COUNT(*) FILTER (WHERE status NOT IN ('completed', 'failed')) as pending
FROM workflow_step_agents WHERE step_id = $1;

-- If pending = 0, complete the step within same transaction
```

**For SQLite:** SQLite uses database-level locking (WAL mode provides reader concurrency but only one writer). This is actually safer -- concurrent writes are serialized. However, if using SQLite in WAL mode with multiple async workers, you need `BEGIN IMMEDIATE` to avoid `SQLITE_BUSY` errors:

```python
async with db.execute("BEGIN IMMEDIATE"):
    # atomic read-modify-write
```

### Additional race: `advance()` itself

Even with the step-level lock, two calls to `advance()` could both find Step B eligible and both try to start it.

**Fix:** Add a status transition guard:

```sql
-- Atomic status transition (only one caller wins)
UPDATE workflow_steps
SET status = 'running', started_at = NOW()
WHERE id = $1 AND status = 'pending'
RETURNING id;

-- If RETURNING returns 0 rows, another caller already started it
```

This compare-and-swap pattern eliminates the race without requiring advisory locks.

### Summary of concurrency requirements

| Operation | Risk | Fix |
|:----------|:-----|:----|
| Multiple agents completing simultaneously | Step never advances (lost update) | `SELECT ... FOR UPDATE` on step row, atomic agent status check |
| Multiple `advance()` calls for same execution | Step started twice | Atomic `UPDATE ... WHERE status = 'pending' RETURNING id` |
| Multiple `complete_execution` calls | Execution completed twice | Same atomic UPDATE pattern |
| Template update during execution | Stale definition | `template_version` snapshot column already handles this (good) |

---

## 8. Summary of Required Changes

### Must Fix (blocks implementation)

1. **Add `UNIQUE(execution_id, step_id)` on `workflow_steps`** -- prevents duplicate steps
2. **Add `UNIQUE(step_id, agent_index)` on `workflow_step_agents`** -- prevents duplicate agent records
3. **Replace `TEXT[]` columns with `TEXT` (JSON array)** -- SQLite compatibility
4. **Add indexes on `jobs.workflow_execution_id` and `jobs.workflow_step_id`** -- FK join performance
5. **Add row-level locking in `handle_agent_result` and atomic status transitions in `advance()`** -- prevents race conditions
6. **Add CHECK constraints on `status` columns** -- data integrity

### Should Fix (improves quality)

7. **Add `workflow_template_versions` table** -- version history (matches existing `skill_versions` pattern)
8. **Standardize timestamp types** -- use `TEXT DEFAULT (datetime('now'))` for SQLite compat, or define a DB-abstraction layer
9. **Add DOWN migration** -- reversibility requirement
10. **Add `timeout_seconds` and `on_failure` columns to `workflow_steps`** -- avoid re-parsing template definition at runtime
11. **Add `created_at` to `workflow_step_agents`** -- debugging
12. **Add retention policy** -- prevent unbounded growth

### Nice to Have

13. **Partition `workflow_steps` by `created_at`** -- only if growth exceeds 10 GB/year
14. **GIN index on `workflow_steps.output`** -- only if cross-step output queries emerge
15. **Add `idx_wf_exec_template` index** -- FK best practice
