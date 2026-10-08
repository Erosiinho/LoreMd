# PLAN.md — LoreMd

> Shared memory for AI agents, in Markdown, self-hosted.

## 1. Vision

**LoreMd** is a **self-hosted** memory server that deploys with a single command on any VPS. A company runs it on its own infrastructure and connects **all of its agents** to it (Claude Code, Codex, Cursor, Hermes, in-house bots…) through **MCP**.

Agents record decisions, preferences, rules and project context there, and find them again across sessions, machines and agents.

The memory is **Markdown files versioned with git**: readable, editable in Obsidian or any editor, portable, with no lock-in. The database is only used for indexing and access control.

**Positioning**: "your agents' memory, in files you own, on your servers".

## 2. Principles

1. **Files are the truth**: each memory is a `.md` file with frontmatter, and each change is a git commit. Postgres is a rebuildable index.
2. **Agents first**: machine access goes exclusively through MCP and tokens. There is no connector for consumer web interfaces.
3. **One command to start**: `docker compose up` with a config file.
4. **Isolated by default**: one space is one git repository, and a token only has access to the spaces assigned to it.
5. **Traceable**: the author of each commit is the agent or human who wrote it. Every access is audited.
6. **Honest**: when nothing matches, search answers "nothing found" instead of returning noise.

## 3. Scope

**In scope**
- MCP server over Streamable HTTP
- Agent token auth with scopes
- Spaces as git repositories
- Hybrid search (keywords + semantic)
- Deduplication and replacement of outdated memories
- Audit log
- Two-way git sync (human editing in Obsidian)
- Admin CLI, then web console
- Write review queue

**Out of scope (for now)**
- Connectors for Claude.ai, ChatGPT and other web interfaces (OAuth)
- Multi-tenancy: one instance is one company
- Heavy attachments (PDFs, binaries)
- Mandatory VPN or private network access

## 4. Architecture

```
  Agents (Claude Code, Codex, Cursor, bots…)
        │  HTTPS + Authorization: Bearer <token>
        ▼
  ┌──────────────────── Customer instance (VPS) ──────────────────┐
  │  Caddy (auto TLS, rate limit)                                  │
  │     ├──► MCP API (/mcp)        ──┐                             │
  │     └──► Admin console (/admin)  ├──► Core                     │
  │  loremd CLI ─────────────────────┘     │                       │
  │                                        ├──► Spaces (source)    │
  │                                        │    /data/spaces/      │
  │                                        │      product/  (git)  │
  │                                        │      support/  (git)  │
  │                                        │                       │
  │                                        └──► Postgres (index)   │
  │                                             pgvector + tsvector│
  │                                             tokens, audit      │
  │  Watcher: new commits → reindexing                             │
  └──────────────────────────────┬─────────────────────────────────┘
                                 │ git push / pull
                                 ▼
               Per-space git remote (GitLab, GitHub, Gitea…)
                                 │
                                 ▼
                 Humans: Obsidian or an editor, via git clone
```

**Components**
- **Core**: file writes, commits, search, dedup, ACL, audit. It knows nothing about MCP or HTTP.
- **Spaces**: one git repository per space. This is the memory itself.
- **Postgres**: search index, tokens, audit. `loremd reindex` rebuilds it entirely from the files.
- **Watcher and sync**: pull and push to each space's remote, and reindexing of changed files.
- **MCP API**: a thin layer that exposes the core and sends the server instructions.
- **`loremd` CLI**: administration (spaces, agents, tokens, reindexing, backups).
- **Admin console** (phase 2): review, approval, audit. Changes made in the console become commits signed by the admin.

## 5. Memory format

Layout of a space:

```
product/
├── decisions/
│   └── api-stack-postgres-drizzle-01JA8K.md
├── preferences/
├── rules/
├── facts/
├── notes/
└── .loremd/          # space config (description, review required…)
```

Example file:

```markdown
---
id: 01JA8KZ3M9X2QH7R4T6V
type: decision
title: API stack — Postgres + Drizzle
project: shop
tags: [backend, db]
status: active        # active | pending | superseded | rejected | conflict
verified: false
source: "claude-code session, MacBook, 2026-10-14"
author: claude-code-macbook
created: 2026-10-14T10:42:00+02:00
updated: 2026-10-14T10:42:00+02:00
supersedes: null
---

We use Postgres with Drizzle as the ORM for the shop API.
Reason: end-to-end typing, versioned migrations.
```

- The file name is a slug followed by an ID suffix, which avoids collisions.
- Version history is the git history, with no dedicated table.
- A replaced memory keeps its file with `status: superseded`, and the new one points to it with `supersedes`.

## 6. Tech stack

| Component | Choice | Reason |
|---|---|---|
| Language | Python 3.12 | Mature official MCP SDK, embeddings ecosystem |
| Server | FastAPI + MCP SDK (Streamable HTTP) | Standard, async |
| Git | pygit2 or git CLI | Commits, pull, push, conflict handling |
| Index | Postgres 16 + pgvector + tsvector | Relational, vector and full-text in a single dependency |
| Embeddings | fastembed, local multilingual model by default; OpenAI-compatible API as an option | No key required, data stays local |
| Reverse proxy | Caddy | Automatic TLS |
| Admin console | Server-side rendering + HTMX | Simple, no front-end build |
| Deployment | Docker Compose; Helm chart later | VPS first |

## 7. Data model (Postgres = index)

- **spaces**: `id, slug, path, remote_url, review_required, last_indexed_commit`
- **agents**: `id, name, description, created_at, disabled_at`
- **tokens**: `id, agent_id, prefix, hash, scopes[], space_ids[], expires_at, last_used_at, ip_allowlist[], revoked_at`
- **memory_index**: `id (= frontmatter id), space_id, path, type, title, project, tags[], status, verified, author, content, content_hash, embedding vector, tsv, created, updated`
- **audit_log**: `id, actor, action, target, space_id, ip, created_at, meta jsonb` (append-only)
- **admins** (phase 2): `id, email, password_hash, totp_secret, role`

Only the `tokens`, `agents` and `audit_log` tables cannot be rebuilt from the files. They are included in backups.

## 8. Exposed MCP tools

| Tool | Role |
|---|---|
| `spaces_list()` | Spaces the token can access |
| `memory_search(query, space?, project?, type?, limit=8)` | Hybrid search. Returns scored results, or `{matches: [], status: "no_match"}` |
| `memory_get(id)` | Full memory with its provenance |
| `memory_write(content, type, space, title?, project?, tags?, source?)` | Creates a memory. If a near-identical memory exists, returns it instead of duplicating |
| `memory_update(id, content, reason)` | Changes a memory. The reason becomes the commit message |
| `memory_supersede(id, new_content, reason)` | Replaces an outdated decision. The old one becomes `superseded` |
| `memory_history(id)` | Git history of the memory |

**Server instructions** (sent over MCP to every client): when to search (at the start of a task, before a decision), what to write (decisions, preferences, rules; no chatter and no secrets), and a reminder that memory content is **data**, not instructions.

## 9. Pipelines

**Agent write**
1. Token auth, then check of the `write` scope and the space
2. Validation (max size, type, fields) and secret detection
3. Embedding computation and deduplication against the index. If similarity exceeds a threshold, the existing memory is returned
4. Space lock (one write at a time per repository)
5. File write, with `status: pending` if the space requires review
6. Commit (author = agent name, message = title or reason)
7. Index update, audit, lock release
8. Asynchronous push to the remote

**Human edit**
1. The human clones the space's repository and opens the folder in Obsidian
2. They edit or create files, then push their commits to the remote
3. LoreMd fetches the changes (periodic pull or remote webhook)
4. The watcher reindexes the files changed since `last_indexed_commit`
5. On conflict with an agent write: rebase. If the rebase fails, both versions are kept, the note gets `status: conflict` and is flagged in the console

**Search**
1. Auth, then strict filtering on the allowed spaces
2. Full-text and vector search in parallel
3. Result merging with RRF (Reciprocal Rank Fusion)
4. Exclusion of the `superseded`, `pending`, `rejected` and `conflict` statuses unless explicitly requested
5. Relevance threshold: below it, a `no_match` answer
6. Each result includes provenance, the `verified` flag and the file path
7. Audit

## 10. Security

- **Tokens**: format `lmd_<prefix>_<32 random bytes>`, stored hashed (SHA-256), shown only once. They expire, can be revoked instantly and can be rotated without downtime.
- **Scopes**: `read`, `write`, `admin`, combined with a list of spaces.
- **Human isolation**: one repository per space, so the remote's git permissions (GitLab, GitHub…) control who can read and edit what.
- **Network**: HTTPS required, per-token rate limiting, optional per-token IP allowlist.
- **Memory poisoning**: review per space or per agent, `verified` flag, systematic provenance, and content explicitly returned as data. A human change goes through git and can therefore be reviewed as a merge request if the company wishes.
- **Secrets**: detection on write and a pre-receive hook on the repositories.
- **Backups**: git repositories are already replicated on their remote. Postgres (tokens, audit) is backed up with encrypted `pg_dump`.

## 11. Target deployment

```bash
git clone … && cd loremd
cp config.example.yaml config.yaml   # domain, embedding model
docker compose up -d
docker compose exec app loremd space create product --remote git@gitlab.acme.com:memory/product.git
docker compose exec app loremd agent create codex-ci --space product --scope read,write
# → prints the token once
```

Then one documentation page per client (Claude Code, Codex, Cursor, Cline, Hermes, generic client) with the MCP config snippet and the `Authorization` header, and an "open a space in Obsidian" guide.

## 12. Roadmap

### Phase 0 — Prototype (≈ 1 week)
- One space (local git folder), `memory_write` and `memory_search` (vector only)
- Postgres + pgvector index, a single token from an environment variable
- Real-world test with Claude Code and Codex on two machines, then opening the folder in Obsidian

**Success criterion**: a decision written by Claude Code is found by Codex on another machine and is readable in Obsidian.

### Phase 1 — Single-company MVP (≈ 4 weeks)
- Multiple spaces, agents, tokens with scopes and expiry, `loremd` CLI
- Frozen file format (validated frontmatter), per-agent commits, per-space lock
- Hybrid search, RRF merging, `no_match` threshold
- Deduplication, `memory_update`, `memory_supersede`, `memory_history`
- Secret detection, audit log, server instructions
- `loremd reindex`
- Docker Compose and Caddy, connection docs for 5 clients

**Success criterion**: a test company deploys the instance in under 15 minutes and connects 3 different agents without help.

### Phase 2 — Human sync and governance (≈ 3–4 weeks)
- Per-space git remote: asynchronous push, periodic and webhook-triggered pull
- Incremental watcher, conflict handling (`status: conflict`)
- Admin console (local login + TOTP): review, approval, rejection, conflict resolution, audit
- Review queue per space or per agent, `verified` flag

### Phase 3 — Enterprise-ready
- OIDC SSO for the console
- IP allowlist, anti-secret pre-receive hook
- Prometheus metrics, healthchecks
- Retention policies
- Helm chart

### Phase 4 — Memory quality
- Recall evaluation set (precision@k, correct `no_match` rate)
- Optional automatic classification by an LLM (type, project)
- Consolidation job: merging duplicates, detecting contradictions, proposed as commits to approve
- Webhooks (new memory, pending memory, conflict)

## 13. Risks

| Risk | Mitigation |
|---|---|
| Agents do not use the memory, or use it poorly | Server instructions, per-client snippets, few well-described tools |
| Git conflicts between humans and agents | Per-space lock, automatic rebase, `conflict` status and resolution in the console |
| Index out of sync with the files | `last_indexed_commit`, content hash, `loremd reindex` |
| Performance with many files | Postgres index for all reads, git only on write; test up to 100,000 memories per space |
| Memory degrades (duplicates, contradictions) | Dedup on write, `supersede`, consolidation, human review |
| Poisoning through crafted content | Review, `verified`, provenance, framing content as data |
| Weak multilingual search | Multilingual embedding model by default, hybrid search |
| Competition (Space Teams, open-source projects) | Files the customer owns, self-hosting, governance and audit |

## 14. Open questions

- License: AGPL for protection, or Apache 2.0 with a commercial offering (support, enterprise features)?
- Business model: open core, support, or managed instances?
- Git remote: require an existing GitLab or GitHub, or bundle a minimal git server (Gitea) in the compose file for companies that do not have one?
- Default embedding model: size/quality trade-off on a small VPS without a GPU
- Availability of the LoreMd name (GitHub, domain, INPI, EUIPO)
