-- Phase 2: sync state, per-agent review, admin console.

-- Review can be required per agent as well as per space.
ALTER TABLE agents ADD COLUMN IF NOT EXISTS review_required boolean NOT NULL DEFAULT false;

-- Outcome of the last sync with the remote, shown in the console. Not
-- rebuildable, but nothing is lost with it either: the next sync rewrites it.
ALTER TABLE spaces ADD COLUMN IF NOT EXISTS last_synced_at timestamptz;
ALTER TABLE spaces ADD COLUMN IF NOT EXISTS last_sync_error text;

-- Humans who log into the console. Like tokens, they cannot be rebuilt from
-- the files: include them in backups.
CREATE TABLE IF NOT EXISTS admins (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    email           text NOT NULL UNIQUE,
    password_hash   text NOT NULL,
    totp_secret     text NOT NULL,
    -- Last TOTP time step accepted: a code cannot be replayed.
    totp_last_step  bigint,
    role            text NOT NULL DEFAULT 'admin' CHECK (role IN ('admin', 'reviewer')),
    created_at      timestamptz NOT NULL DEFAULT now(),
    last_login_at   timestamptz,
    disabled_at     timestamptz
);

-- Server-side sessions: the cookie holds a random token, the table its hash.
-- Logging out or disabling an admin takes effect at once.
CREATE TABLE IF NOT EXISTS admin_sessions (
    id          text PRIMARY KEY,
    admin_id    uuid NOT NULL REFERENCES admins(id) ON DELETE CASCADE,
    csrf_token  text NOT NULL,
    ip          inet,
    created_at  timestamptz NOT NULL DEFAULT now(),
    expires_at  timestamptz NOT NULL
);

CREATE INDEX IF NOT EXISTS admin_sessions_admin_idx ON admin_sessions (admin_id);
