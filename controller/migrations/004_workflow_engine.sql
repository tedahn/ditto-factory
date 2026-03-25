-- Migration 004: Workflow Engine
-- Two-state workflow engine tables for deterministic multi-step orchestration.
-- SQLite compatible. Postgres equivalents noted in comments.

-- ============================================================
-- Workflow Templates: versioned, CRUD-managed definitions
-- ============================================================
CREATE TABLE IF NOT EXISTS workflow_templates (
    id              TEXT PRIMARY KEY,                            -- UUID
    slug            TEXT UNIQUE NOT NULL,                        -- human-readable identifier
    name            TEXT NOT NULL,                               -- display name
    description     TEXT,
    version         INTEGER NOT NULL DEFAULT 1,
    definition      TEXT NOT NULL,                               -- Postgres: JSONB NOT NULL
    parameter_schema TEXT,                                       -- Postgres: JSONB
    is_active       BOOLEAN NOT NULL DEFAULT 1,
    created_by      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),     -- Postgres: TIMESTAMPTZ DEFAULT now()
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))      -- Postgres: TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_wf_tmpl_slug
    ON workflow_templates(slug) WHERE is_active = 1;

-- ============================================================
-- Workflow Template Versions: immutable version history
-- (mirrors skill_versions pattern from 002_skill_registry)
-- ============================================================
CREATE TABLE IF NOT EXISTS workflow_template_versions (
    id              TEXT PRIMARY KEY,                            -- UUID
    template_id     TEXT NOT NULL REFERENCES workflow_templates(id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    definition      TEXT NOT NULL,                               -- Postgres: JSONB NOT NULL
    parameter_schema TEXT,                                       -- Postgres: JSONB
    changelog       TEXT,
    created_by      TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),     -- Postgres: TIMESTAMPTZ DEFAULT now()
    UNIQUE (template_id, version)
);

-- ============================================================
-- Workflow Executions: one per workflow invocation
-- ============================================================
CREATE TABLE IF NOT EXISTS workflow_executions (
    id                TEXT PRIMARY KEY,                          -- UUID
    template_id       TEXT NOT NULL REFERENCES workflow_templates(id),
    template_version  INTEGER NOT NULL,                          -- snapshot of version at execution time
    thread_id         TEXT NOT NULL,                             -- links to existing threads table
    parameters        TEXT NOT NULL DEFAULT '{}',                -- Postgres: JSONB NOT NULL
    status            TEXT NOT NULL DEFAULT 'pending',
                      -- pending | running | completed | failed | cancelled
    started_at        TEXT,                                      -- Postgres: TIMESTAMPTZ
    completed_at      TEXT,                                      -- Postgres: TIMESTAMPTZ
    result            TEXT,                                      -- Postgres: JSONB
    error             TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now'))     -- Postgres: TIMESTAMPTZ DEFAULT now()
);

-- Only index active executions (the ones we poll)
CREATE INDEX IF NOT EXISTS idx_wf_exec_active
    ON workflow_executions(status)
    WHERE status IN ('pending', 'running');

CREATE INDEX IF NOT EXISTS idx_wf_exec_thread
    ON workflow_executions(thread_id);

-- ============================================================
-- Workflow Steps: individual units of work within an execution
-- ============================================================
CREATE TABLE IF NOT EXISTS workflow_steps (
    id              TEXT PRIMARY KEY,                            -- UUID
    execution_id    TEXT NOT NULL REFERENCES workflow_executions(id) ON DELETE CASCADE,
    step_id         TEXT NOT NULL,                               -- from template definition (e.g., "search")
    step_type       TEXT NOT NULL,                               -- fan_out | sequential | aggregate |
                                                                 -- transform | report | conditional
    status          TEXT NOT NULL DEFAULT 'pending',
                    -- pending | running | completed | failed | skipped
    input           TEXT,                                        -- Postgres: JSONB
    output          TEXT,                                        -- Postgres: JSONB
    agent_jobs      TEXT DEFAULT '[]',                           -- Postgres: TEXT[] DEFAULT '{}'
                                                                 -- JSON array of K8s job names
    started_at      TEXT,                                        -- Postgres: TIMESTAMPTZ
    completed_at    TEXT,                                        -- Postgres: TIMESTAMPTZ
    error           TEXT,
    retry_count     INTEGER NOT NULL DEFAULT 0,
    max_retries     INTEGER NOT NULL DEFAULT 2,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))       -- Postgres: TIMESTAMPTZ DEFAULT now()
);

-- Prevent duplicate step_ids within the same execution
CREATE UNIQUE INDEX IF NOT EXISTS idx_wf_steps_exec_step
    ON workflow_steps(execution_id, step_id);

CREATE INDEX IF NOT EXISTS idx_wf_steps_exec
    ON workflow_steps(execution_id);

CREATE INDEX IF NOT EXISTS idx_wf_steps_active
    ON workflow_steps(execution_id, status)
    WHERE status IN ('pending', 'running');

-- ============================================================
-- Workflow Step Agents: individual agent results within a fan-out
-- ============================================================
CREATE TABLE IF NOT EXISTS workflow_step_agents (
    id              TEXT PRIMARY KEY,                            -- UUID
    step_id         TEXT NOT NULL REFERENCES workflow_steps(id) ON DELETE CASCADE,
    agent_index     INTEGER NOT NULL,                            -- 0-based index within fan-out
    k8s_job_name    TEXT,
    thread_id       TEXT NOT NULL,                               -- agent's own thread_id
    status          TEXT NOT NULL DEFAULT 'pending',
    input           TEXT,                                        -- Postgres: JSONB
    output          TEXT,                                        -- Postgres: JSONB
    started_at      TEXT,                                        -- Postgres: TIMESTAMPTZ
    completed_at    TEXT,                                        -- Postgres: TIMESTAMPTZ
    error           TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_wf_step_agents_unique
    ON workflow_step_agents(step_id, agent_index);

CREATE INDEX IF NOT EXISTS idx_wf_step_agents_step
    ON workflow_step_agents(step_id);

-- ============================================================
-- ALTER jobs table: link jobs to workflow executions/steps
-- Uses a check to avoid errors if columns already exist.
-- SQLite does not support IF NOT EXISTS on ALTER TABLE,
-- so these will error harmlessly if re-run on an existing DB.
-- Application code should handle this gracefully.
-- ============================================================
ALTER TABLE jobs ADD COLUMN workflow_execution_id TEXT REFERENCES workflow_executions(id);
ALTER TABLE jobs ADD COLUMN workflow_step_id TEXT REFERENCES workflow_steps(id);
ALTER TABLE jobs ADD COLUMN workflow_agent_index INTEGER;

CREATE INDEX IF NOT EXISTS idx_jobs_wf_exec
    ON jobs(workflow_execution_id) WHERE workflow_execution_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_jobs_wf_step
    ON jobs(workflow_step_id) WHERE workflow_step_id IS NOT NULL;
