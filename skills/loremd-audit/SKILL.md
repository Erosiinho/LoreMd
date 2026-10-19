---
name: loremd-audit
description: >-
  Review the health of a LoreMd space, read-only. Use when the user asks to
  "audit the memory", "clean up memories", "find duplicates", "find stale or
  outdated decisions", "check for contradictions", or before a big refactor of
  a project's conventions.
---

# LoreMd audit: find stale, duplicate and conflicting memories

Reads a space and proposes a repair plan. It changes nothing on its own: every
fix waits for the user's explicit approval.

## What it does

- Lists memories that look **stale** (old, unverified, about something that has
  moved on), **duplicated** (the same idea written twice), or **conflicting**
  (two active memories that say opposite things).
- Proposes, for each finding, one concrete action with the exact tool call.

## What it never does

- Never calls `memory_write`, `memory_update`, `memory_supersede` or
  `handoff_save` during the audit. It is read-only until the user approves a
  plan, item by item or as a whole.
- Never deletes anything: LoreMd has no delete for agents, and a fix is a
  supersede or an update, which keeps history in git.
- Never decides which of two conflicting memories is right. It shows both and
  asks.
- Never touches memories with `status: conflict`: those are for a human in the
  admin console.

## Workflow

1. Ask for the scope if it is not given: space, and optionally project or topic.
2. Survey the space by topic. Run a handful of broad searches, one per area the
   project covers (stack, deployment, testing, conventions, security…):

   ```
   memory_search(query="database", space="product", project="shop", limit=20)
   memory_search(query="deployment", space="product", project="shop", limit=20)
   ```

   Also run `context_resume(space="product", project="shop")` to see the
   current handoff and the decisions and rules it relies on.
3. For each candidate finding, read the memories in full with `memory_get(id)`
   and, when age or authorship matters, `memory_history(id)`.
4. Classify:
   - **Duplicate**: two memories state the same idea. Propose keeping the more
     complete one and superseding the other with a pointer to it.
   - **Conflict**: two active memories contradict each other. Propose
     superseding the outdated one, and say which you think it is and why, but
     leave the choice to the user.
   - **Stale**: unverified, old, or contradicted by the current handoff or
     code. Propose an update, a supersede, or asking a human to verify it in
     the console.
   - **Malformed**: several ideas in one memory, no reason, or unreadable on
     its own. Propose a rewrite with `memory_update`.
5. Present the report as a table: finding, memory ids and titles, evidence,
   proposed call. Example row:

   | Finding | Memories | Evidence | Proposed fix |
   |---|---|---|---|
   | Conflict | `01JA…` "ORM: Drizzle", `01JB…` "ORM: Prisma" | both active, Prisma is newer | `memory_supersede(id="01JA…", new_content="…", reason="replaced by Prisma")` |

6. Stop and wait. Apply only the items the user approves, one call each, then
   report the new ids.

## Boundaries

- Search only returns `active` memories: pending ones are in the review queue,
  which belongs to humans (`loremd review list`, `/admin/review`).
- Large spaces: say how much was covered ("12 searches, 64 memories read")
  rather than claiming completeness.
