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
  will say so.
- Set `status: rejected` on a wrong memory rather than deleting it — the history
  stays readable.
- Create a memory by hand: put it in the right folder, with at least `id`,
  `type`, `title`, `created` and `updated`. Generate a ULID `id` with
  `python -c "from loremd.ids import new_ulid; print(new_ulid())"`.

## How changes get back into LoreMd

```bash
git add . && git commit -m "review product decisions" && git push
```

The server fetches the commits (`loremd space sync`, periodic pull or webhook)
and reindexes only the files in the diff.

If there is a conflict with an agent write, LoreMd rebases. If the rebase fails,
both versions are kept and the note gets `status: conflict` so someone can
resolve it.

## What not to do

- Do not rename files without a reason: the name contains the ID suffix.
- Do not change the `id` field, which links the file to the index.
- Do not commit secrets: a remote-side hook will reject them.
