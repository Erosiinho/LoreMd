# Cursor

Dans `~/.cursor/mcp.json` (global) ou `.cursor/mcp.json` (projet) :

```json
{
  "mcpServers": {
    "loremd": {
      "url": "https://memoire.acme.com/mcp",
      "headers": { "Authorization": "Bearer lmd_xxxxxxxx_…" }
    }
  }
}
```

Un fichier `.cursor/mcp.json` versionné ne doit **pas** contenir le token :
utiliser le fichier global, ou une variable d'environnement si la version de
Cursor le permet.
