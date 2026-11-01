# LoreMd agent skills

The MCP server tells agents *what* the tools do. These skills tell them *when*
to use them: one `SKILL.md` per workflow, with a trigger-oriented description,
what the skill does and never does, the ordered steps, the exact tool calls,
and its boundaries.

| Skill | Use it when | Tools |
|---|---|---|
| [`loremd-session`](loremd-session/SKILL.md) | Starting a session on a project, or ending one / checkpointing | `context_resume`, `handoff_save` |
| [`loremd-recall`](loremd-recall/SKILL.md) | The user asks what was decided, or before a technical choice | `memory_search`, `memory_get` |
| [`loremd-remember`](loremd-remember/SKILL.md) | A durable decision, rule, preference or fact should be recorded | `memory_search`, `memory_write`, `memory_update`, `memory_supersede` |
| [`loremd-audit`](loremd-audit/SKILL.md) | Reviewing a space for stale, duplicate or conflicting memories (read-only) | `memory_search`, `memory_get`, `memory_history`, `context_resume` |

## Example requests

| Request | Skill |
|---|---|
| "Resume shop." / "Where were we on the auth refactor?" | `loremd-session` → `context_resume` |
| "Checkpoint." / "Save where we are, I'm stopping here." | `loremd-session` → `handoff_save` |
| "What did we decide about the ORM?" | `loremd-recall` |
| "Do we have a rule about logging tokens?" | `loremd-recall` |
| "Remember that we deploy on Tuesdays only, to keep Fridays quiet." | `loremd-remember` |
| "We're moving from Drizzle to Prisma, update the memory." | `loremd-remember` → `memory_supersede` |
| "Audit the shop project's memory." | `loremd-audit` |

## Installing

The skills only need the LoreMd MCP server to be connected
([docs/clients/](../docs/clients/README.md)).

**Claude Code** loads skills from `.claude/skills/` in a project, or from
`~/.claude/skills/` for every project:

```bash
mkdir -p ~/.claude/skills
cp -r skills/loremd-* ~/.claude/skills/
```

**Other clients** (Codex, Cursor, Cline…): if your version loads skill
folders, copy the `loremd-*` folders there. Otherwise point the client's
standing instructions at them — `AGENTS.md` for Codex, a rule in
`.cursor/rules/` for Cursor — for example:

```markdown
For LoreMd memory, follow skills/loremd-session/SKILL.md at the start and end of
every session, and skills/loremd-recall/SKILL.md before any technical decision.
```

The files are plain Markdown with YAML front matter (`name`, `description`), so
they read the same in any client, and in Obsidian.
