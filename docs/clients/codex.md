# Codex

Dans `~/.codex/config.toml` :

```toml
[mcp_servers.loremd]
url = "https://memoire.acme.com/mcp"

[mcp_servers.loremd.http_headers]
Authorization = "Bearer lmd_xxxxxxxx_…"
```

Créer un token dédié à la machine ou au pipeline :

```bash
loremd agent create codex-ci --space produit --scope read,write --expires-days 90
```

Pour un agent de CI, préférer `--scope read` : une mémoire écrite par un job
automatique est rarement relue par un humain.
