# LoreMd

> Shared memory for AI agents, in Markdown, self-hosted.

Agents (Claude Code, Codex, Cursor, in-house bots…) record decisions,
preferences, rules and project context through **MCP**, and find them again
across sessions, machines and agents.

The memory is **Markdown files versioned with git**: readable in Obsidian,
editable by hand, portable. Postgres is only used for indexing and access
control — it can be rebuilt with `loremd reindex`.

The full product plan is in [PLAN.md](PLAN.md).

## Getting started

```bash
cp config.example.yaml config.yaml   # domain, embedding model
cp .env.example .env                 # passwords, bootstrap token
docker compose up -d

docker compose exec app loremd space create product \
    --remote git@gitlab.acme.com:memory/product.git
docker compose exec app loremd agent create codex-ci --space product --scope read,write
# → prints the token once
docker compose exec app loremd admin create alice@acme.com
# → prompts for a password, prints the TOTP secret once
```

The admin console is then at `https://memory.acme.com/admin`.

For a space with a remote, the container needs an SSH deploy key with write
access, plus the remote's host key: put `id_ed25519` and a `known_hosts`
(`ssh-keyscan gitlab.acme.com > deploy/ssh/known_hosts`) in `deploy/ssh/`.

Check the installation:

```bash
docker compose exec app loremd doctor
curl https://memory.acme.com/healthz
```

Then connect an agent: see [docs/clients/](docs/clients/README.md).

## Architecture

```
Agents ──HTTPS + Bearer──► Caddy ──► MCP API (/mcp) ──────► Core ──► Spaces (git) ◄──sync──► remote
Admins ──password + TOTP──►       ──► Console (/admin) ──►    │                              ▲
Forge  ──signed webhook───►       ──► /hooks/git/<space> ──►  └──► Postgres (index)          │
                                                                    Humans: Obsidian ──git push┘
```

| Package | Role |
|---|---|
| `loremd.core` | Files, commits, search, dedup, ACL, audit, sync with the remote, review. Knows nothing about MCP or HTTP. |
| `loremd.db` | Postgres index (pgvector + tsvector), migrations, queries. |
| `loremd.embeddings` | Local fastembed by default, OpenAI-compatible API as an option. |
| `loremd.auth` | Agent tokens, scopes, per-space isolation; console admins (scrypt passwords, TOTP, sessions). |
| `loremd.api` | MCP server (Streamable HTTP), admin console, push webhook, ASGI application. |
| `loremd.cli` | Administration: spaces, agents, tokens, review, conflicts, admins, reindexing, audit. |

## Memory format

A space is a git repository; a memory is a file.

```
product/
├── decisions/api-stack-postgres-drizzle-abcdef.md
├── preferences/
├── rules/
├── facts/
├── notes/
└── .loremd/space.yaml
```

```markdown
---
id: 01JA8KZ3M9X2QH7R4T6VABCDEF
type: decision
title: API stack — Postgres + Drizzle
project: shop
tags: [backend, db]
status: active
verified: false
source: claude-code session
author: claude-code-macbook
created: '2026-10-14T10:42:00+02:00'
updated: '2026-10-14T10:42:00+02:00'
supersedes: null
---

We use Postgres with Drizzle as the ORM for the shop API.
```

Statuses: `active`, `pending`, `superseded`, `rejected`, `conflict`. Only
`active` memories show up in search.

## Human sync and governance

- **Sync.** Each commit is pushed to the space's remote in the background
  (commits close together are batched). Every `sync.interval_seconds`, and at
  once when the forge calls the webhook, LoreMd fetches, rebases its commits on
  the remote and reindexes only the files in the diff.
- **Conflicts.** When the rebase fails, nothing is lost: the remote version
  keeps the path, the server version is kept alongside it
  (`…-conflict-xxxxxx.md`), both get `status: conflict`, leave search, and
  cannot be changed by agents until someone resolves them in the console or
  with `loremd conflict resolve`.
- **Review.** Turn it on per space (`loremd space review <slug> --on`, stored in
  `.loremd/space.yaml`) or per agent (`loremd agent review <name> --on`). Writes
  then land as `pending`; an approval publishes them with `verified: true`, a
  rejection keeps the file as `rejected`. A replacement under review leaves the
  old memory active until it is approved.
- **Console.** `/admin`: review queue, memory history, `verified` toggle,
  side-by-side conflict resolution (keep one version, merge by hand, or
  discard), per-space sync status, audit log. Local accounts with password and
  TOTP; the `reviewer` role sees everything but the audit log. Every decision
  is a commit signed with the admin's email.

Setting up the webhook and working in Obsidian: [docs/obsidian.md](docs/obsidian.md).

## MCP tools

| Tool | Role |
|---|---|
| `spaces_list()` | Spaces the token can access |
| `memory_search(query, space?, project?, type?, limit)` | Hybrid search; `status: no_match` when nothing matches |
| `memory_get(id)` | Full memory with its provenance |
| `memory_write(content, type, space, …)` | Creates a memory, or returns the existing near-duplicate |
| `memory_update(id, content, reason)` | Changes a memory; `reason` becomes the commit message |
| `memory_supersede(id, new_content, reason)` | Replaces a memory; the old one becomes `superseded` |
| `memory_history(id)` | Git history of the memory |

## Commands

```bash
loremd space create <slug> [--remote URL] [--review]
loremd space list
loremd space sync [<slug>] [--pull/--no-pull] [--push/--no-push]
loremd space remote <slug> <url>
loremd space review <slug> --on|--off
loremd agent create <name> --space <slug> --scope read,write [--review]
loremd agent review <name> --on|--off
loremd token create <agent> --space <slug> [--expires-days 90]
loremd token revoke <prefix>
loremd review list [--space <slug>]
loremd review approve|reject|verify <id>
loremd conflict list [--space <slug>]
loremd conflict resolve <id> --keep memory|copy|discard
loremd admin create <email> [--role admin|reviewer]
loremd admin list | disable <email> | password <email> | reset-totp <email>
loremd reindex [<slug>]
loremd search "<query>" [--space <slug>]
loremd audit --limit 50
loremd doctor
loremd serve
```

## Development

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,embeddings]"
pytest
ruff check src tests
mypy
```

The default suite needs neither Postgres nor the network: it covers the file
format, identifiers, secret detection, tokens, rate limiting, permissions,
passwords, TOTP, webhook signatures, conflict merging and git operations.

The tests under `tests/integration/` exercise the real SQL, the real git
repositories (sync against a bare remote included), the MCP server over HTTP
and the console. They are skipped unless
`LOREMD_TEST_DSN` points at a Postgres with pgvector:

```bash
docker run -d --rm -p 5432:5432 -e POSTGRES_PASSWORD=loremd \
    --name loremd-test pgvector/pgvector:pg16
LOREMD_TEST_DSN=postgresql://postgres:loremd@localhost:5432/postgres pytest
```

Those tests recreate the `public` schema on each run, so point them at a
throwaway database.

## Security

- Tokens `lmd_<prefix>_<secret>`, stored as SHA-256 hashes, shown once,
  revocable, with expiry and an optional IP allowlist.
- Scopes `read` / `write` / `admin`, combined with a list of spaces: a token
  sees nothing outside its own spaces.
- A request with no bearer token gets a `401` before the MCP handshake; the
  token itself is verified on every call, so revoking one takes effect at once.
- Per-token rate limit (`limits` in `config.yaml`), so one looping agent cannot
  saturate the instance.
- Secret detection on write: the call is rejected and nothing is committed.
- Commits are signed with the agent's name through `GIT_AUTHOR_*`, so the
  author in `git log` is the agent even when the host has its own git identity.
- Memory content is returned to agents as **data**, never as instructions (see
  `loremd/instructions.py`).
- Console: scrypt password hashes, TOTP with replay protection, server-side
  sessions in a `SameSite=Strict`, `HttpOnly` cookie, CSRF token on every form,
  a strict Content-Security-Policy, agent content always rendered escaped, and
  login attempts limited per client IP and per email.
- Webhook: authenticated with a shared secret (GitHub/Gitea HMAC signature or
  GitLab token); it only schedules a sync, and is off until a secret is set.

## Status

Phases 0–2 of the plan: core, MCP over Streamable HTTP, hybrid search with
`no_match`, dedup, agents/tokens/scopes, audit, per-token rate limiting, CLI,
Docker Compose and Caddy; two-way git sync (background push, periodic and
webhook pull, incremental reindexing, conflict handling), review queue per
space or per agent, `verified` flag, and the admin console with TOTP.

Coming next (phase 3): OIDC SSO for the console, anti-secret pre-receive hook,
metrics, retention, Helm chart.
