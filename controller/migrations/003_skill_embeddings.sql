-- Migration 003: Add embedding columns for Phase 2 semantic search
--
-- For SQLite (dev): store embeddings as JSON text arrays of floats.
--   Cosine similarity is computed in Python (see SkillRegistry._cosine_similarity).
--
-- For Postgres with pgvector (production): replace TEXT with vector(1024):
--   ALTER TABLE skills ADD COLUMN embedding vector(1024);
--   ALTER TABLE skill_versions ADD COLUMN embedding vector(1024);
--   ALTER TABLE skill_usage ADD COLUMN task_embedding vector(1024);
--   CREATE INDEX idx_skills_embedding ON skills USING ivfflat (embedding vector_cosine_ops);
--   Then use: ORDER BY embedding <=> $1 LIMIT 20

ALTER TABLE skills ADD COLUMN embedding TEXT;          -- JSON array of floats
ALTER TABLE skill_versions ADD COLUMN embedding TEXT;  -- JSON array of floats
ALTER TABLE skill_usage ADD COLUMN task_embedding TEXT; -- JSON array of floats
