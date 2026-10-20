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
```

Check the installation:

```bash
docker compose exec app loremd doctor
curl https://memory.acme.com/healthz
```

Then connect an agent: see [docs/clients/](docs/clients/README.md).

## Architecture

```
Agents ──HTTPS + Bearer──► Caddy ──► MCP API (/mcp) ──► Core ──► Spaces (git)
                                                           └──► Postgres (index)
Humans ──git clone──► per-space git remote ──► Obsidian
```

| Package | Role |
|---|---|
| `loremd.core` | Files, commits, search, dedup, ACL, audit. Knows nothing about MCP or HTTP. |
| `loremd.db` | Postgres index (pgvector + tsvector), migrations, queries. |
| `loremd.embeddings` | Local fastembed by default, OpenAI-compatible API as an option. |
| `loremd.auth` | Agent tokens, scopes, per-space isolation. |
| `loremd.api` | MCP server (Streamable HTTP) and ASGI application. |
| `loremd.cli` | Administration: spaces, agents, tokens, reindexing, audit. |

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
loremd space sync [<slug>] [--pull/--no-pull] [--push]
loremd agent create <name> --space <slug> --scope read,write
loremd token create <agent> --space <slug> [--expires-days 90]
loremd token revoke <prefix>
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
format, identifiers, secret detection, tokens, rate limiting, permissions and
git operations.

The tests under `tests/integration/` exercise the real SQL, the real git
repositories and the MCP server over HTTP. They are skipped unless
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

## Status

Phases 0–1 of the plan: core, MCP over Streamable HTTP, hybrid search with
`no_match`, dedup, agents/tokens/scopes, audit, per-token rate limiting, CLI,
Docker Compose and Caddy.

Coming next (phase 2): full two-way git sync (watcher, webhooks, conflict
handling), admin console, review queue. Then SSO, metrics and retention.
