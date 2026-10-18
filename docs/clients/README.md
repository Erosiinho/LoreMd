# Connecting an agent to LoreMd

Every client speaks the same protocol: **MCP over Streamable HTTP**, at
`https://<your-domain>/mcp`, with an `Authorization: Bearer <token>` header.

Create a token:

```bash
loremd agent create claude-code-macbook --space product --scope read,write
```

The token is shown only once. It is stored hashed on the server.

- [Claude Code](claude-code.md)
- [Codex](codex.md)
- [Cursor](cursor.md)
- [Cline](cline.md)
- [Generic client](generic.md)
- [Opening a space in Obsidian](../obsidian.md)

## Checking the connection

```bash
curl -s https://memory.acme.com/healthz
curl -s -X POST https://memory.acme.com/mcp \
  -H "Authorization: Bearer $LOREMD_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Seven tools should show up: `spaces_list`, `memory_search`, `memory_get`,
`memory_write`, `memory_update`, `memory_supersede`, `memory_history`.

## Troubleshooting

| Message | Cause |
|---|---|
| `401 unauthorized: missing token` | The `Authorization` header does not reach the server. Check the proxy, and that the client sends it on every request. |
| `unauthorized: unknown token` | Token copied wrong, or revoked and recreated. |
| `unauthorized: token expired` | The token reached its `--expires-days`: create a new one, then revoke the old one. |
| `forbidden: space not allowed for this token` | The token does not include this space: `loremd token create … --space <slug>`. |
| `forbidden: token lacks the write scope` | Read-only token. |
| `secret_detected` | The content contains a key or a password: the write is rejected. |
| `rate_limited` | The token went over `limits.requests_per_minute`. The message says how long to wait. |
