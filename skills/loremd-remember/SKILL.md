---
name: loremd-remember
description: >-
  Record one durable decision, rule, preference or fact in LoreMd. Use when
  the user says "remember that…", "note that we decided…", "from now on…",
  "add a rule", or when a lasting choice was just made in the session and
  should outlive it.
---

# LoreMd remember: record one durable fact

Writes a single memory that another session or another agent will find later.
One memory per idea, stated so it makes sense on its own.

## What it does

- Checks for an existing memory on the same subject, then either writes a new
  one, refines the existing one, or replaces it.

## What it never does

- Never writes chatter, transient state ("I'm now editing file X"), copied file
  contents, or anything that belongs in a handoff (`loremd-session`).
- Never writes a secret: API keys, passwords, tokens. The write is rejected and
  must not be retried with the secret "obfuscated".
- Never writes a second memory that contradicts an existing one: it supersedes
  the old one instead.
- Never writes several ideas in one memory, or one idea split across several.

## Workflow

1. Write the memory as **one standalone sentence plus its reason**. It must make
   sense with no conversation around it:
   - Good: "The shop API uses Postgres with Drizzle as the ORM, for end-to-end
     typing with the TypeScript front end."
   - Bad: "We went with the second option." / "Use Drizzle."
2. Pick the type: `decision` (a choice made), `rule` (a constraint to follow),
   `preference` (a habit or taste), `fact` (a stable truth about the project),
   `note` (anything else durable).
3. Search for duplicates and contradictions first:

   ```
   memory_search(query="ORM shop API", space="product", project="shop", type="decision")
   ```

4. Then exactly one of:
   - **Nothing relevant** (`no_match`):

     ```
     memory_write(
       content="The shop API uses Postgres with Drizzle as the ORM, for end-to-end typing.",
       type="decision", space="product", project="shop",
       title="Shop API — Postgres + Drizzle", tags=["backend", "db"],
       source="claude-code session"
     )
     ```

   - **Same idea, needs a correction or detail**:
     `memory_update(id="…", content="…", reason="pin the Drizzle version")`.
   - **Contradicts it** (the choice changed):
     `memory_supersede(id="…", new_content="…", reason="moved to Prisma in October")`.
     The old memory becomes `superseded` and the new one points to it.
5. Read the response:
   - `created: false` with `duplicate_of`: the memory already existed. Do not
     write again; update or supersede it if needed.
   - `status: pending`: the space requires review; tell the user it will show
     up once a human approves it.
   - `secret_detected`: remove the secret entirely and tell the user why.
6. Confirm to the user in one line, with the id.

## Boundaries

- Requires a token with the `write` scope; a read-only token gets `forbidden`.
- A memory in `conflict` cannot be changed by an agent: a human resolves it in
  the console.
- When unsure whether something is durable, ask the user rather than writing.
