# Connecter un agent à LoreMd

Tous les clients parlent le même protocole : **MCP en Streamable HTTP**, sur
`https://<votre-domaine>/mcp`, avec un en-tête `Authorization: Bearer <token>`.

Créer un token :

```bash
loremd agent create claude-code-macbook --space produit --scope read,write
```

Le token est affiché une seule fois. Il est stocké haché côté serveur.

- [Claude Code](claude-code.md)
- [Codex](codex.md)
- [Cursor](cursor.md)
- [Cline](cline.md)
- [Client générique](generic.md)
- [Ouvrir un espace dans Obsidian](../obsidian.md)

## Vérifier la connexion

```bash
curl -s https://memoire.acme.com/healthz
curl -s -X POST https://memoire.acme.com/mcp \
  -H "Authorization: Bearer $LOREMD_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

Sept outils doivent apparaître : `spaces_list`, `memory_search`, `memory_get`,
`memory_write`, `memory_update`, `memory_supersede`, `memory_history`.

## En cas d'erreur

| Message | Cause |
|---|---|
| `unauthorized: token manquant` | L'en-tête `Authorization` n'arrive pas jusqu'au serveur. |
| `unauthorized: token inconnu` | Token mal copié, ou révoqué puis recréé. |
| `forbidden: espace non autorisé` | Le token n'a pas cet espace : `loremd token create … --space <slug>`. |
| `forbidden: le token n'a pas le scope write` | Token en lecture seule. |
| `secret_detected` | Le contenu contient une clé ou un mot de passe : l'écriture est refusée. |
