# LoreMd

> Shared memory for AI agents, in Markdown, self-hosted.

Agents (Claude Code, Codex, Cursor, in-house bots…) record decisions,
preferences, rules and project context through **MCP**, find them again across
sessions, machines and agents, and pick up where the last session stopped.

The memory is **Markdown files versioned with git**: readable in Obsidian,
editable by hand, portable. Postgres only holds the index and access control,
and `loremd reindex` rebuilds it.

## Getting started

```bash
cp config.example.yaml config.yaml   # public_url, embedding model, limits
cp .env.example .env                 # passwords, bootstrap token
docker compose up -d

docker compose exec app loremd space create product \
    --remote git@gitlab.acme.com:memory/product.git
docker compose exec app loremd agent create codex-ci --space product --scope read,write
# → prints the token once
docker compose exec app loremd admin create alice@acme.com
# → prompts for a password, prints the TOTP secret once

docker compose exec app loremd doctor
curl https://memory.acme.com/healthz
```

The console is at `https://memory.acme.com/admin`. A space with a remote needs
an SSH deploy key and the remote's host key in `deploy/ssh/` (`id_ed25519`,
`known_hosts`).

Then connect an agent ([docs/clients/](docs/clients/README.md)) and give it the
[agent skills](skills/README.md) so it knows when to recall, remember and hand
off.

## Architecture

```
Agents ──HTTPS + Bearer──► Caddy ──► MCP API (/mcp) ──────► Core ──► Spaces (git) ◄──sync──► remote
Admins ──password + TOTP──►       ──► Console (/admin) ──►    │                              ▲
Forge  ──signed webhook───►       ──► /hooks/git/<space> ──►  └──► Postgres (index)          │
                                                                    Humans: Obsidian ──git push┘
```

| Package | Role |
|---|---|
| `loremd.core` | Files, commits, search, dedup, ACL, audit, sync, review. No MCP or HTTP. |
| `loremd.db` | Postgres index (pgvector + tsvector), migrations, queries. |
| `loremd.embeddings` | Local fastembed by default, OpenAI-compatible API as an option. |
| `loremd.auth` | Agent tokens and scopes; console admins (passwords, TOTP, sessions). |
| `loremd.api` | MCP server (Streamable HTTP), admin console, webhook. |
| `loremd.cli` | Administration commands. |

## Memory format

A space is a git repository; a memory is a file under `decisions/`,
`preferences/`, `rules/`, `facts/`, `notes/` or `handoffs/`.

```markdown
---
id: 01JA8KZ3M9X2QH7R4T6VABCDEF
type: decision
title: API stack — Postgres + Drizzle
project: shop
tags: [backend, db]
status: active
verified: false
author: claude-code-macbook
created: '2026-10-14T10:42:00+02:00'
updated: '2026-10-14T10:42:00+02:00'
supersedes: null
---

We use Postgres with Drizzle as the ORM for the shop API.
```

Statuses: `active`, `pending`, `superseded`, `rejected`, `conflict`. Only
`active` memories show up in search.

## Continue work across sessions

Before a session ends, the agent saves a **handoff** with `handoff_save`; the
next session, on any machine and with any agent, calls `context_resume` and
gets the latest handoff plus the relevant decisions and rules.

- A handoff is a memory of type `handoff`, rendered by the server into fixed
  sections (Summary, Decisions, Progress, Open questions, Next actions).
- One thread per project: a new handoff supersedes the previous one, which
  stays in git.
- Same rules as any memory: secret detection, review, signed commits, audit.
  `sources` lists the memories it relies on.
- `context_resume` caps its output at `max_chars`, lists what did not fit in
  `omitted`, and answers `status: no_match` when there is nothing to resume.
- Handoffs stay out of `memory_search` unless `type="handoff"` is asked for.

The [`loremd-session`](skills/loremd-session/SKILL.md) skill tells agents when
to call both tools.

## Human sync and governance

- **Sync.** Commits are pushed to the space's remote in the background. LoreMd
  pulls every `sync.interval_seconds` and on the forge webhook, rebases its
  commits and reindexes only the changed files.
- **Conflicts.** A failed rebase loses nothing: both versions are kept, marked
  `conflict`, and leave search until someone resolves them in the console or
  with `loremd conflict resolve`.
- **Review.** Per space or per agent. Writes land as `pending`; approval
  publishes them with `verified: true`, rejection keeps them as `rejected`.
- **Console.** `/admin`: overview, memory browser per space, sessions (each
  project's handoff thread), review queue, history, conflict resolution, sync
  status, agents and tokens, audit log. Server-rendered, no JavaScript. Local
  accounts with password and TOTP; the `reviewer` role cannot see agents or the
  audit log.

Webhook setup and editing from Obsidian: [docs/obsidian.md](docs/obsidian.md).

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
| `handoff_save(space, title, summary, …)` | Saves where the session stopped |
| `context_resume(space, project?, query?, limit=5, max_chars=12000)` | Latest handoff plus relevant decisions and rules |

## Commands

```bash
loremd space create <slug> [--remote URL] [--review]
loremd space list | remote <slug> <url> | review <slug> --on|--off
loremd space sync [<slug>] [--pull/--no-pull] [--push/--no-push]
loremd agent create <name> --space <slug> --scope read,write [--review]
loremd agent list | review <name> --on|--off | disable <name>
loremd token create <agent> --space <slug> [--expires-days 90]
loremd token list | revoke <prefix>
loremd review list [--space <slug>] | approve|reject|verify <id>
loremd conflict list [--space <slug>] | resolve <id> --keep memory|copy|discard
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
pytest && ruff check src tests && mypy
```

The default suite needs neither Postgres nor the network. The tests under
`tests/integration/` (SQL, git sync, MCP over HTTP, console) run when
`LOREMD_TEST_DSN` points at a **throwaway** Postgres with pgvector — they
recreate the `public` schema:

```bash
docker run -d --rm -p 5432:5432 -e POSTGRES_PASSWORD=loremd \
    --name loremd-test pgvector/pgvector:pg16
LOREMD_TEST_DSN=postgresql://postgres:loremd@localhost:5432/postgres pytest
```

## Security

- Tokens `lmd_<prefix>_<secret>`, stored hashed, shown once, revocable, with
  expiry and an optional IP allowlist. Scopes `read` / `write` / `admin`, limited
  to the token's spaces.
- `/mcp` verifies the token on every request, before the MCP handshake, so a
  revocation takes effect at once. Repeated failed authentications from one IP
  get a `429` (`limits.failed_auth_per_minute`).
- `/mcp` only answers on the host of `public_url`, localhost or
  `allowed_hosts` (DNS rebinding guard).
- Per-token rate limit, secret detection on write, commits authored by the
  agent, memory content returned to agents as data, never as instructions.
- Console: scrypt passwords, TOTP with replay protection, `SameSite=Strict`,
  `HttpOnly` session cookie (`Secure` over HTTPS), CSRF tokens, strict CSP,
  escaped content, login attempts limited per IP and per email.
- Webhook: GitHub/Gitea HMAC signature or GitLab token; off until a secret is
  set, and it only schedules a sync.

## Status

Done: core, MCP, hybrid search, dedup, tokens and
scopes, audit, rate limiting, CLI, Docker Compose and Caddy; two-way git sync
with conflict handling, review queue, `verified` flag, admin console with TOTP;
handoffs, `context_resume` and the agent skills.

Next: OIDC SSO for the console, anti-secret pre-receive hook, Prometheus
metrics, retention policies, Helm chart; then memory quality (recall evaluation
set, optional LLM classification, consolidation of duplicates and
contradictions, outgoing webhooks).
