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
