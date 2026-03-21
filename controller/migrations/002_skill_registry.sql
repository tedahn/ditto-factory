-- Migration 002: Skill Registry (Phase 1 - Tag-based matching)
-- No pgvector required. Embedding columns added in Phase 2.

CREATE TABLE IF NOT EXISTS skills (
    id              TEXT PRIMARY KEY,
    name            VARCHAR(128) NOT NULL UNIQUE,
    slug            VARCHAR(128) NOT NULL UNIQUE,
    description     TEXT NOT NULL,
    content         TEXT NOT NULL,
    language        TEXT DEFAULT '[]',    -- JSON array stored as text
    domain          TEXT DEFAULT '[]',
    requires        TEXT DEFAULT '[]',
    tags            TEXT DEFAULT '[]',
    org_id          VARCHAR(128),
    repo_pattern    VARCHAR(256),
    version         INTEGER NOT NULL DEFAULT 1,
    created_by      VARCHAR(128) NOT NULL DEFAULT '',
    is_active       BOOLEAN NOT NULL DEFAULT 1,
    is_default      BOOLEAN NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_skills_slug ON skills(slug);
CREATE INDEX IF NOT EXISTS idx_skills_active ON skills(is_active);
CREATE INDEX IF NOT EXISTS idx_skills_default ON skills(is_default);

CREATE TABLE IF NOT EXISTS skill_versions (
    id              TEXT PRIMARY KEY,
    skill_id        TEXT NOT NULL REFERENCES skills(id) ON DELETE CASCADE,
    version         INTEGER NOT NULL,
    content         TEXT NOT NULL,
    description     TEXT NOT NULL,
    changelog       TEXT,
    created_by      VARCHAR(128) NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (skill_id, version)
);

CREATE TABLE IF NOT EXISTS skill_usage (
    id              TEXT PRIMARY KEY,
    skill_id        TEXT NOT NULL REFERENCES skills(id),
    thread_id       VARCHAR(128) NOT NULL,
    job_id          VARCHAR(128) NOT NULL,
    task_source     VARCHAR(32) NOT NULL,
    repo_owner      VARCHAR(128),
    repo_name       VARCHAR(128),
    was_selected    BOOLEAN NOT NULL DEFAULT 1,
    exit_code       INTEGER,
    commit_count    INTEGER,
    pr_created      BOOLEAN DEFAULT 0,
    injected_at     TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_usage_skill ON skill_usage(skill_id);
CREATE INDEX IF NOT EXISTS idx_usage_thread ON skill_usage(thread_id);

CREATE TABLE IF NOT EXISTS agent_types (
    id              TEXT PRIMARY KEY,
    name            VARCHAR(128) NOT NULL UNIQUE,
    image           VARCHAR(256) NOT NULL,
    description     TEXT,
    capabilities    TEXT NOT NULL DEFAULT '[]',
    resource_profile TEXT NOT NULL DEFAULT '{}',
    is_default      BOOLEAN NOT NULL DEFAULT 0,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

INSERT OR IGNORE INTO agent_types (id, name, image, capabilities, is_default)
VALUES ('default', 'general', 'ditto-factory-agent:latest', '[]', 1);
