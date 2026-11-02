# Codex

In `~/.codex/config.toml`:

```toml
[mcp_servers.loremd]
url = "https://memory.acme.com/mcp"

[mcp_servers.loremd.http_headers]
Authorization = "Bearer lmd_xxxxxxxx_…"
```

Create a token dedicated to the machine or the pipeline:

```bash
loremd agent create codex-ci --space product --scope read,write --expires-days 90
```

For a CI agent, prefer `--scope read`: a memory written by an automated job is
rarely reviewed by a human.

## Skills

Give Codex the [LoreMd skills](../../skills/README.md): copy the `loremd-*`
folders into its skills folder if your version loads them, or reference them
from `AGENTS.md`:

```markdown
For LoreMd memory, follow skills/loremd-session/SKILL.md at the start and end of
every session, and skills/loremd-recall/SKILL.md before any technical decision.
```

A handoff saved by Claude Code on a laptop is what `context_resume` returns to
Codex in CI: both read the same space.
