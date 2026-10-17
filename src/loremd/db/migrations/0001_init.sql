-- LoreMd — schéma d'index.
-- Postgres n'est qu'un index : spaces et memory_index sont entièrement
-- reconstructibles depuis les fichiers (`loremd reindex`). Seules agents,
-- tokens et audit_log portent de la donnée propre.
--
-- __EMBEDDING_DIM__ est remplacé à l'application de la migration par la
-- dimension du modèle d'embeddings configuré.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS spaces (
    id                   uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    slug                 text NOT NULL UNIQUE,
    path                 text NOT NULL,
    description          text,
    remote_url           text,
    review_required      boolean NOT NULL DEFAULT false,
    last_indexed_commit  text,
    created_at           timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS agents (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    name         text NOT NULL UNIQUE,
    description  text,
    created_at   timestamptz NOT NULL DEFAULT now(),
    disabled_at  timestamptz
);

CREATE TABLE IF NOT EXISTS tokens (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    agent_id      uuid NOT NULL REFERENCES agents(id) ON DELETE CASCADE,
    prefix        text NOT NULL UNIQUE,
    hash          text NOT NULL,
    scopes        text[] NOT NULL DEFAULT '{}',
    space_ids     uuid[] NOT NULL DEFAULT '{}',
    ip_allowlist  text[] NOT NULL DEFAULT '{}',
    expires_at    timestamptz,
    last_used_at  timestamptz,
    revoked_at    timestamptz,
    created_at    timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS tokens_agent_idx ON tokens (agent_id);

CREATE TABLE IF NOT EXISTS memory_index (
    -- id = identifiant ULID du frontmatter, la source reste le fichier.
    id            text PRIMARY KEY,
    space_id      uuid NOT NULL REFERENCES spaces(id) ON DELETE CASCADE,
    path          text NOT NULL,
    type          text NOT NULL,
    title         text NOT NULL,
    project       text,
    tags          text[] NOT NULL DEFAULT '{}',
    status        text NOT NULL DEFAULT 'active',
    verified      boolean NOT NULL DEFAULT false,
    author        text,
    source        text,
    supersedes    text,
    content       text NOT NULL,
    content_hash  text NOT NULL,
    embedding     vector(__EMBEDDING_DIM__),
    tsv           tsvector,
    created       timestamptz NOT NULL DEFAULT now(),
    updated       timestamptz NOT NULL DEFAULT now(),
    indexed_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (space_id, path)
);

CREATE INDEX IF NOT EXISTS memory_space_status_idx ON memory_index (space_id, status);
CREATE INDEX IF NOT EXISTS memory_project_idx ON memory_index (space_id, project);
CREATE INDEX IF NOT EXISTS memory_tsv_idx ON memory_index USING gin (tsv);
CREATE INDEX IF NOT EXISTS memory_embedding_idx
    ON memory_index USING hnsw (embedding vector_cosine_ops);

CREATE TABLE IF NOT EXISTS audit_log (
    id         bigserial PRIMARY KEY,
    actor      text NOT NULL,
    action     text NOT NULL,
    target     text,
    -- Pas de clé étrangère : le journal survit à la suppression d'un espace.
    space_id   uuid,
    ip         inet,
    meta       jsonb NOT NULL DEFAULT '{}',
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS audit_created_idx ON audit_log (created_at DESC);
CREATE INDEX IF NOT EXISTS audit_actor_idx ON audit_log (actor, created_at DESC);

-- Ajout seul : pas de UPDATE ni de DELETE sur le journal d'audit.
CREATE OR REPLACE RULE audit_log_no_update AS ON UPDATE TO audit_log DO INSTEAD NOTHING;
CREATE OR REPLACE RULE audit_log_no_delete AS ON DELETE TO audit_log DO INSTEAD NOTHING;
