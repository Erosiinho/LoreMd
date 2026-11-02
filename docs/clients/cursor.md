# Cursor

In `~/.cursor/mcp.json` (global) or `.cursor/mcp.json` (project):

```json
{
  "mcpServers": {
    "loremd": {
      "url": "https://memory.acme.com/mcp",
      "headers": { "Authorization": "Bearer lmd_xxxxxxxx_…" }
    }
  }
}
```

A versioned `.cursor/mcp.json` must **not** contain the token: use the global
file, or an environment variable if your Cursor version supports it.

## Skills

Add the [LoreMd skills](../../skills/README.md) as a project rule, for
instance `.cursor/rules/loremd.mdc`:

```markdown
---
description: Shared memory through LoreMd
alwaysApply: true
---
Follow skills/loremd-session/SKILL.md at the start and end of every session,
skills/loremd-recall/SKILL.md before any technical decision, and
skills/loremd-remember/SKILL.md to record a durable decision.
```
