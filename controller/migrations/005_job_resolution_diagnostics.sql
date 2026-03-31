-- Add fields that exist on the Job model but were never persisted
ALTER TABLE jobs ADD COLUMN agent_type TEXT NOT NULL DEFAULT 'general';
ALTER TABLE jobs ADD COLUMN skills_injected TEXT NOT NULL DEFAULT '[]';
ALTER TABLE jobs ADD COLUMN resolution_diagnostics TEXT;

CREATE INDEX IF NOT EXISTS idx_jobs_agent_type ON jobs(agent_type);
