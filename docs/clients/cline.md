# Cline

In the extension's MCP settings (`cline_mcp_settings.json`):

```json
{
  "mcpServers": {
    "loremd": {
      "type": "streamableHttp",
      "url": "https://memory.acme.com/mcp",
      "headers": { "Authorization": "Bearer lmd_xxxxxxxx_…" },
      "disabled": false
    }
  }
}
```

Leave `memory_search` on auto-approve and keep writes on manual approval at
first: it is the best way to see what the agent considers worth remembering.

## Skills

Reference the [LoreMd skills](../../skills/README.md) from your `.clinerules`
so Cline calls `context_resume` when a task starts and `handoff_save` before it
finishes. `context_resume` is read-only and safe to auto-approve.
