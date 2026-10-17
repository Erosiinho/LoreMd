# LoreMd

> Mémoire partagée pour agents IA, en Markdown, auto-hébergée.

Les agents (Claude Code, Codex, Cursor, bots maison…) enregistrent décisions,
préférences, règles et contexte projet via **MCP**, et les retrouvent d'une
session, d'une machine ou d'un agent à l'autre.

La mémoire, ce sont **des fichiers Markdown versionnés par git** : lisibles dans
Obsidian, modifiables à la main, portables. Postgres ne sert qu'à indexer et à
contrôler les accès — il est reconstructible par `loremd reindex`.

Le plan produit complet est dans [PLAN.md](PLAN.md).

## Démarrage

```bash
cp config.example.yaml config.yaml   # domaine, modèle d'embeddings
cp .env.example .env                 # mots de passe, token d'amorçage
docker compose up -d

docker compose exec app loremd space create produit \
    --remote git@gitlab.acme.com:memoire/produit.git
docker compose exec app loremd agent create codex-ci --space produit --scope read,write
# → affiche le token une seule fois
```

Vérifier l'installation :

```bash
docker compose exec app loremd doctor
curl https://memoire.acme.com/healthz
```

Puis connecter un agent : voir [docs/clients/](docs/clients/README.md).

## Architecture

```
Agents ──HTTPS + Bearer──► Caddy ──► API MCP (/mcp) ──► Core ──► Espaces (git)
                                                           └──► Postgres (index)
Humains ──git clone──► remote git par espace ──► Obsidian
```

| Paquet | Rôle |
|---|---|
| `loremd.core` | Fichiers, commits, recherche, dédup, ACL, audit. Ne connaît ni MCP ni HTTP. |
| `loremd.db` | Index Postgres (pgvector + tsvector), migrations, requêtes. |
| `loremd.embeddings` | fastembed en local par défaut, API compatible OpenAI en option. |
| `loremd.auth` | Tokens d'agent, scopes, cloisonnement par espace. |
| `loremd.api` | Serveur MCP (Streamable HTTP) et application ASGI. |
| `loremd.cli` | Administration : espaces, agents, tokens, réindexation, audit. |

## Format des mémoires

Un espace est un dépôt git ; une mémoire est un fichier.

```
produit/
├── decisions/api-stack-postgres-drizzle-abcdef.md
├── preferences/
├── rules/
├── facts/
├── notes/
└── .loremd/space.yaml
```

```markdown
---
id: 01JA8KZ3M9X2QH7R4T6VABCDEF
type: decision
title: Stack API — Postgres + Drizzle
project: shop
tags: [backend, db]
status: active
verified: false
source: session claude-code
author: claude-code-macbook
created: '2026-10-14T10:42:00+02:00'
updated: '2026-10-14T10:42:00+02:00'
supersedes: null
---

On utilise Postgres avec Drizzle comme ORM pour l'API du shop.
```

Statuts : `active`, `pending`, `superseded`, `rejected`, `conflict`. Seules les
mémoires `active` ressortent dans la recherche.

## Outils MCP

| Outil | Rôle |
|---|---|
| `spaces_list()` | Espaces accessibles au token |
| `memory_search(query, space?, project?, type?, limit)` | Recherche hybride ; `status: no_match` si rien ne correspond |
| `memory_get(id)` | Mémoire complète avec sa provenance |
| `memory_write(content, type, space, …)` | Crée une mémoire, ou renvoie le quasi-doublon existant |
| `memory_update(id, content, reason)` | Modifie ; `reason` devient le message de commit |
| `memory_supersede(id, new_content, reason)` | Remplace ; l'ancienne passe en `superseded` |
| `memory_history(id)` | Historique git de la mémoire |

## Commandes

```bash
loremd space create <slug> [--remote URL] [--review]
loremd space list
loremd space sync [<slug>] [--pull/--no-pull] [--push]
loremd agent create <nom> --space <slug> --scope read,write
loremd token create <agent> --space <slug> [--expires-days 90]
loremd token revoke <prefixe>
loremd reindex [<slug>]
loremd search "<requête>" [--space <slug>]
loremd audit --limit 50
loremd doctor
loremd serve
```

## Développement

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,embeddings]"
pytest
ruff check src tests
```

Les tests de cette suite ne demandent ni Postgres ni réseau : ils couvrent le
format de fichier, les identifiants, la détection de secrets, les tokens, les
droits et les opérations git. Les chemins qui touchent la base se testent contre
une instance Postgres avec pgvector (`docker compose up postgres`).

## Sécurité

- Tokens `lmd_<prefix>_<secret>`, stockés hachés en SHA-256, affichés une seule
  fois, révocables, avec expiration et liste d'IP optionnelle.
- Scopes `read` / `write` / `admin`, combinés à une liste d'espaces : un token ne
  voit rien en dehors des siens.
- Détection de secrets à l'écriture : l'appel est refusé, rien n'est committé.
- Le contenu des mémoires est renvoyé aux agents comme une **donnée**, jamais
  comme une instruction (voir `loremd/instructions.py`).

## État d'avancement

Phase 0–1 du plan : core, MCP, index hybride, tokens, CLI, Docker Compose.
À venir : synchro git bidirectionnelle complète (watcher, webhooks, conflits),
console d'administration, SSO et métriques.
