# Claude Code

```bash
claude mcp add --transport http loremd https://memoire.acme.com/mcp \
  --header "Authorization: Bearer lmd_xxxxxxxx_…"
```

Ou dans `~/.claude/settings.json` :

```json
{
  "mcpServers": {
    "loremd": {
      "type": "http",
      "url": "https://memoire.acme.com/mcp",
      "headers": { "Authorization": "Bearer lmd_xxxxxxxx_…" }
    }
  }
}
```

Vérifier avec `/mcp` dans une session : le serveur `loremd` doit être
`connected`, et ses instructions apparaissent dans le contexte.

Conseil : donner un nom d'agent par machine (`claude-code-macbook`,
`claude-code-vps`). C'est ce nom qui signe les commits, donc ce qu'on lit dans
`git log` et dans le journal d'audit.
