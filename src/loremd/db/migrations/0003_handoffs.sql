-- Handoffs: the memories a handoff relies on, mirrored from its frontmatter
-- so `context_resume` reads them from the index. Rebuilt by `loremd reindex`.
ALTER TABLE memory_index ADD COLUMN IF NOT EXISTS sources text[] NOT NULL DEFAULT '{}';

CREATE INDEX IF NOT EXISTS memory_type_updated_idx
    ON memory_index (space_id, type, status, updated DESC);
