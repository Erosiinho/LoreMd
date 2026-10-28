# Opening a space in Obsidian

A space is an ordinary git repository. A human clones it and opens it as an
Obsidian vault.

```bash
git clone git@gitlab.acme.com:memory/product.git ~/Obsidian/product
```

In Obsidian: *Open folder as vault* → `~/Obsidian/product`.

## What you can do

- Read, fix and enrich memories. The frontmatter shows up as properties:
  `type`, `project`, `tags`, `status`, `verified`.
- Set `verified: true` on a reviewed memory: the provenance returned to agents
  will say so. (Or use the toggle on the memory's page in the console.)
- Set `status: rejected` on a wrong memory rather than deleting it — the history
  stays readable.
- Create a memory by hand: put it in the right folder, with at least `id`,
  `type`, `title`, `created` and `updated`. Generate a ULID `id` with
  `python -c "from loremd.ids import new_ulid; print(new_ulid())"`.

## How changes get back into LoreMd

```bash
git add . && git commit -m "review product decisions" && git push
```

The server picks the commits up on its own: it pulls every
`sync.interval_seconds` (60 by default), at once when the forge calls the
webhook below, or on demand with `loremd space sync` or the *Sync now* button
of the console. Only the files in the diff are reindexed.

Agent writes go the other way: each commit is pushed to the remote within a
couple of seconds, so a `git pull` brings them into the vault.

Changing `.loremd/space.yaml` (for instance `review_required: true`) works the
same way: the server applies it at the next sync.

## Conflicts

If a human and an agent changed the same memory, LoreMd first tries to rebase
its commits on yours. If that fails, nothing is thrown away:

- your version keeps the path, and gets `status: conflict`;
- the server's version is kept next to it, as `…-conflict-xxxxxx.md`, also
  `status: conflict`, with `conflict_of` pointing to yours.

Both leave search and agents cannot change them. Resolve the conflict in the
console (*Conflicts*: keep one version, merge the two by hand, or discard both),
or with `loremd conflict resolve <id> --keep memory|copy|discard`. The result
is a single active, verified memory at the original path; git keeps every
version.

## Pushes trigger a sync: the webhook

Without a webhook, your changes arrive within `sync.interval_seconds`. To make
it immediate, set a secret (`LOREMD_SYNC__WEBHOOK_SECRET` in `.env`, e.g.
`openssl rand -hex 32`) and register a push webhook on the space's repository:

| Forge | URL | Secret |
|---|---|---|
| GitHub | `https://memory.acme.com/hooks/git/<space>`, content type `application/json`, *push* events | *Secret* field |
| GitLab | same URL, *Push events* | *Secret token* field |
| Gitea / Forgejo | same URL, *Push events* | *Secret* field |

Pushes to other branches than `spaces.default_branch` are ignored.

## Review

When review is on for the space or for the agent, new memories arrive with
`status: pending`. Approve or reject them in the console (*Review*) or with
`loremd review approve|reject <id>`. Approval sets `verified: true`; rejection
keeps the file with `status: rejected`.

## What not to do

- Do not rename files without a reason: the name contains the ID suffix.
- Do not change the `id` field, which links the file to the index.
- Do not commit secrets: a remote-side hook will reject them.
