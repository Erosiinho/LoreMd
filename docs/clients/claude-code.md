# Claude Code

```bash
claude mcp add --transport http loremd https://memory.acme.com/mcp \
  --header "Authorization: Bearer lmd_xxxxxxxx_…"
```

Or in `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "loremd": {
      "type": "http",
      "url": "https://memory.acme.com/mcp",
      "headers": { "Authorization": "Bearer lmd_xxxxxxxx_…" }
    }
  }
}
```

Check with `/mcp` in a session: the `loremd` server should be `connected`, and
its instructions show up in the context.

Tip: use one agent name per machine (`claude-code-macbook`, `claude-code-vps`).
That name signs the commits, so it is what you read in `git log` and in the
audit log.
