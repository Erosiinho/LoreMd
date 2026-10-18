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
