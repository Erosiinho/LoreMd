# Cline

Dans les réglages MCP de l'extension (`cline_mcp_settings.json`) :

```json
{
  "mcpServers": {
    "loremd": {
      "type": "streamableHttp",
      "url": "https://memoire.acme.com/mcp",
      "headers": { "Authorization": "Bearer lmd_xxxxxxxx_…" },
      "disabled": false
    }
  }
}
```

Laisser `memory_search` en approbation automatique et garder les écritures en
approbation manuelle au début : c'est le meilleur moyen de voir ce que l'agent
juge digne d'être mémorisé.
