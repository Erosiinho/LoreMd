# PLAN.md — LoreMd

> Mémoire partagée pour agents IA, en Markdown, auto-hébergée.

## 1. Vision

**LoreMd** est un serveur de mémoire **auto-hébergé**, déployable en une commande sur n'importe quel VPS. Une entreprise l'instancie chez elle pour y connecter **tous ses agents** (Claude Code, Codex, Cursor, Hermes, bots maison…) via **MCP**.

Les agents y enregistrent décisions, préférences, règles et contexte projet, et les retrouvent d'une session, d'une machine ou d'un agent à l'autre.

La mémoire, ce sont **des fichiers Markdown versionnés par git** : lisibles, modifiables dans Obsidian ou n'importe quel éditeur, portables, sans enfermement. La base de données ne sert qu'à indexer et à contrôler les accès.

**Positionnement** : « la mémoire de vos agents, en fichiers que vous possédez, sur vos serveurs ».

## 2. Principes

1. **Les fichiers sont la vérité** : chaque mémoire est un fichier `.md` avec frontmatter, et chaque changement est un commit git. Postgres est un index reconstructible.
2. **Agents d'abord** : l'accès machine passe uniquement par MCP et des tokens. Il n'y a pas de connecteur pour les interfaces web grand public.
3. **Une commande pour démarrer** : `docker compose up` avec un fichier de config.
4. **Cloisonné par défaut** : un espace correspond à un dépôt git, et un token n'a accès qu'aux espaces qui lui sont attribués.
5. **Traçable** : l'auteur de chaque commit est l'agent ou l'humain qui a écrit. Chaque accès est audité.
6. **Honnête** : quand rien ne correspond, la recherche répond « rien trouvé » au lieu de renvoyer du bruit.

## 3. Périmètre

**Dans le périmètre**
- Serveur MCP en Streamable HTTP
- Auth par tokens d'agent avec scopes
- Espaces sous forme de dépôts git
- Recherche hybride (mots-clés + sémantique)
- Déduplication et remplacement de mémoires obsolètes
- Journal d'audit
- Synchronisation git bidirectionnelle (édition humaine dans Obsidian)
- CLI d'administration, puis console web
- File de validation des écritures

**Hors périmètre (pour l'instant)**
- Connecteurs pour Claude.ai, ChatGPT et autres interfaces web (OAuth)
- Multi-tenant : une instance correspond à une entreprise
- Pièces jointes lourdes (PDF, binaires)
- Accès par VPN ou réseau privé obligatoire

## 4. Architecture

```
  Agents (Claude Code, Codex, Cursor, bots…)
        │  HTTPS + Authorization: Bearer <token>
        ▼
  ┌──────────────────── Instance client (VPS) ────────────────────┐
  │  Caddy (TLS auto, rate limit)                                  │
  │     ├──► API MCP (/mcp)        ──┐                             │
  │     └──► Console admin (/admin)  ├──► Core                     │
  │  CLI loremd ─────────────────────┘     │                       │
  │                                        ├──► Espaces (source)   │
  │                                        │    /data/spaces/      │
  │                                        │      produit/  (git)  │
  │                                        │      support/  (git)  │
  │                                        │                       │
  │                                        └──► Postgres (index)   │
  │                                             pgvector + tsvector│
  │                                             tokens, audit      │
  │  Watcher : nouveaux commits → réindexation                     │
  └──────────────────────────────┬─────────────────────────────────┘
                                 │ git push / pull
                                 ▼
               Remote git par espace (GitLab, GitHub, Gitea…)
                                 │
                                 ▼
                 Humains : Obsidian ou éditeur, via git clone
```

**Composants**
- **Core** : écriture de fichiers, commits, recherche, dédup, ACL, audit. Il ne connaît ni MCP ni HTTP.
- **Espaces** : un dépôt git par espace. C'est la mémoire elle-même.
- **Postgres** : index de recherche, tokens, audit. `loremd reindex` le reconstruit entièrement à partir des fichiers.
- **Watcher et synchro** : pull et push vers le remote de chaque espace, et réindexation des fichiers modifiés.
- **API MCP** : une fine couche qui expose le core et envoie les instructions serveur.
- **CLI `loremd`** : administration (espaces, agents, tokens, réindexation, sauvegarde).
- **Console admin** (phase 2) : relecture, validation, audit. Les modifications faites dans la console deviennent des commits signés par l'admin.

## 5. Format des mémoires

Arborescence d'un espace :

```
produit/
├── decisions/
│   └── api-stack-postgres-drizzle-01JA8K.md
├── preferences/
├── rules/
├── facts/
├── notes/
└── .loremd/          # config de l'espace (description, validation requise…)
```

Exemple de fichier :

```markdown
---
id: 01JA8KZ3M9X2QH7R4T6V
type: decision
title: Stack API — Postgres + Drizzle
project: shop
tags: [backend, db]
status: active        # active | pending | superseded | rejected | conflict
verified: false
source: "session claude-code, MacBook, 2026-10-14"
author: claude-code-macbook
created: 2026-10-14T10:42:00+02:00
updated: 2026-10-14T10:42:00+02:00
supersedes: null
---

On utilise Postgres avec Drizzle comme ORM pour l'API du shop.
Raison : typage de bout en bout, migrations versionnées.
```

- Le nom de fichier est un slug suivi d'un suffixe d'ID, ce qui évite les collisions.
- L'historique des versions est l'historique git, sans table dédiée.
- Une mémoire remplacée garde son fichier avec `status: superseded`, et la nouvelle pointe vers elle avec `supersedes`.

## 6. Stack technique

| Brique | Choix | Raison |
|---|---|---|
| Langage | Python 3.12 | SDK MCP officiel mature, écosystème embeddings |
| Serveur | FastAPI + SDK MCP (Streamable HTTP) | Standard, async |
| Git | pygit2 ou git en CLI | Commits, pull, push, gestion des conflits |
| Index | Postgres 16 + pgvector + tsvector | Relationnel, vecteur et plein texte en une seule dépendance |
| Embeddings | fastembed, modèle multilingue local par défaut ; API compatible OpenAI en option | Aucune clé requise, données qui restent locales |
| Reverse proxy | Caddy | TLS automatique |
| Console admin | Rendu serveur + HTMX | Simple, sans build front |
| Déploiement | Docker Compose ; chart Helm plus tard | VPS d'abord |

## 7. Modèle de données (Postgres = index)

- **spaces** : `id, slug, path, remote_url, review_required, last_indexed_commit`
- **agents** : `id, name, description, created_at, disabled_at`
- **tokens** : `id, agent_id, prefix, hash, scopes[], space_ids[], expires_at, last_used_at, ip_allowlist[], revoked_at`
- **memory_index** : `id (= id du frontmatter), space_id, path, type, title, project, tags[], status, verified, author, content, content_hash, embedding vector, tsv, created, updated`
- **audit_log** : `id, actor, action, target, space_id, ip, created_at, meta jsonb` (en ajout seul)
- **admins** (phase 2) : `id, email, password_hash, totp_secret, role`

Seules les tables `tokens`, `agents` et `audit_log` ne sont pas reconstructibles depuis les fichiers. Elles sont incluses dans les sauvegardes.

## 8. Outils MCP exposés

| Outil | Rôle |
|---|---|
| `spaces_list()` | Espaces accessibles au token |
| `memory_search(query, space?, project?, type?, limit=8)` | Recherche hybride. Renvoie des résultats avec score, ou `{matches: [], status: "no_match"}` |
| `memory_get(id)` | Mémoire complète avec sa provenance |
| `memory_write(content, type, space, title?, project?, tags?, source?)` | Crée une mémoire. Si une mémoire quasi identique existe, la renvoie au lieu de dupliquer |
| `memory_update(id, content, reason)` | Modifie une mémoire. Le motif devient le message du commit |
| `memory_supersede(id, new_content, reason)` | Remplace une décision obsolète. L'ancienne passe en `superseded` |
| `memory_history(id)` | Historique git de la mémoire |

**Instructions serveur** (envoyées par MCP à chaque client) : quand chercher (en début de tâche, avant une décision), quoi écrire (décisions, préférences, règles ; pas de bavardage ni de secrets), et rappel que le contenu des mémoires est une **donnée**, pas une instruction.

## 9. Pipelines

**Écriture par un agent**
1. Auth du token, puis vérification du scope `write` et de l'espace
2. Validation (taille max, type, champs) et détection de secrets
3. Calcul de l'embedding et déduplication contre l'index. Si la similarité dépasse un seuil, on renvoie la mémoire existante
4. Verrou de l'espace (une écriture à la fois par dépôt)
5. Écriture du fichier, avec `status: pending` si l'espace exige une validation
6. Commit (auteur = nom de l'agent, message = titre ou motif)
7. Mise à jour de l'index, audit, libération du verrou
8. Push asynchrone vers le remote

**Édition par un humain**
1. L'humain clone le dépôt de l'espace et ouvre le dossier dans Obsidian
2. Il modifie ou crée des fichiers, puis pousse ses commits sur le remote
3. LoreMd récupère les changements (pull périodique ou webhook du remote)
4. Le watcher réindexe les fichiers modifiés depuis `last_indexed_commit`
5. En cas de conflit avec une écriture d'agent : rebase. Si le rebase échoue, les deux versions sont conservées, la note passe en `status: conflict` et elle est signalée dans la console

**Recherche**
1. Auth, puis filtrage strict sur les espaces autorisés
2. Recherche plein texte et vectorielle en parallèle
3. Fusion des résultats par RRF (Reciprocal Rank Fusion)
4. Exclusion des statuts `superseded`, `pending`, `rejected` et `conflict` sauf demande explicite
5. Seuil de pertinence : en dessous, réponse `no_match`
6. Chaque résultat inclut la provenance, le drapeau `verified` et le chemin du fichier
7. Audit

## 10. Sécurité

- **Tokens** : format `lmd_<prefix>_<32 octets aléatoires>`, stockés hachés (SHA-256), affichés une seule fois. Ils ont une expiration, sont révocables instantanément et peuvent être tournés sans coupure.
- **Scopes** : `read`, `write`, `admin`, combinés avec une liste d'espaces.
- **Cloisonnement des humains** : un dépôt par espace, donc les droits git du remote (GitLab, GitHub…) contrôlent qui peut lire et éditer quoi.
- **Réseau** : HTTPS obligatoire, rate limiting par token, liste d'IP autorisées par token en option.
- **Empoisonnement de mémoire** : validation par espace ou par agent, drapeau `verified`, provenance systématique, et contenu renvoyé explicitement comme donnée. Une modification humaine passe par git et reste donc relisible en merge request si l'entreprise le souhaite.
- **Secrets** : détection à l'écriture et hook pre-receive sur les dépôts.
- **Sauvegardes** : les dépôts git sont déjà répliqués sur leur remote. Postgres (tokens, audit) est sauvegardé par `pg_dump` chiffré.

## 11. Déploiement cible

```bash
git clone … && cd loremd
cp config.example.yaml config.yaml   # domaine, modèle d'embeddings
docker compose up -d
docker compose exec app loremd space create produit --remote git@gitlab.acme.com:memoire/produit.git
docker compose exec app loremd agent create codex-ci --space produit --scope read,write
# → affiche le token une seule fois
```

Puis une page de documentation par client (Claude Code, Codex, Cursor, Cline, Hermes, client générique) avec le snippet de config MCP et le header `Authorization`, et un guide « ouvrir un espace dans Obsidian ».

## 12. Roadmap

### Phase 0 — Prototype (≈ 1 semaine)
- Un espace (dossier git local), `memory_write` et `memory_search` (vectoriel seul)
- Index Postgres + pgvector, un seul token en variable d'environnement
- Test réel avec Claude Code et Codex sur deux machines, puis ouverture du dossier dans Obsidian

**Critère de réussite** : une décision écrite par Claude Code est retrouvée par Codex sur une autre machine et lisible dans Obsidian.

### Phase 1 — MVP mono-entreprise (≈ 4 semaines)
- Espaces multiples, agents, tokens avec scopes et expiration, CLI `loremd`
- Format de fichier figé (frontmatter validé), commits par agent, verrou par espace
- Recherche hybride, fusion RRF, seuil `no_match`
- Déduplication, `memory_update`, `memory_supersede`, `memory_history`
- Détection de secrets, journal d'audit, instructions serveur
- `loremd reindex`
- Docker Compose et Caddy, documentation de connexion pour 5 clients

**Critère de réussite** : une entreprise test déploie l'instance en moins de 15 minutes et connecte 3 agents différents sans aide.

### Phase 2 — Synchro humaine et gouvernance (≈ 3–4 semaines)
- Remote git par espace : push asynchrone, pull périodique et par webhook
- Watcher incrémental, gestion des conflits (`status: conflict`)
- Console admin (login local + TOTP) : relecture, validation, rejet, résolution de conflits, audit
- File de validation par espace ou par agent, drapeau `verified`

### Phase 3 — Prêt pour l'entreprise
- SSO OIDC pour la console
- Liste d'IP autorisées, hook pre-receive anti-secrets
- Métriques Prometheus, healthchecks
- Politiques de rétention
- Chart Helm

### Phase 4 — Qualité de la mémoire
- Jeu d'évaluation du rappel (précision@k, taux de `no_match` correct)
- Classement automatique optionnel par LLM (type, projet)
- Tâche de consolidation : fusion des doublons, détection des contradictions, proposées sous forme de commits à valider
- Webhooks (nouvelle mémoire, mémoire en attente, conflit)

## 13. Risques

| Risque | Mitigation |
|---|---|
| Les agents n'utilisent pas la mémoire ou l'utilisent mal | Instructions serveur, snippets par client, outils peu nombreux et bien décrits |
| Conflits git entre humains et agents | Verrou par espace, rebase automatique, statut `conflict` et résolution dans la console |
| Index désynchronisé des fichiers | `last_indexed_commit`, hash de contenu, `loremd reindex` |
| Performances avec beaucoup de fichiers | Index Postgres pour toutes les lectures, git uniquement à l'écriture ; tester jusqu'à 100 000 mémoires par espace |
| La mémoire se dégrade (doublons, contradictions) | Déduplication à l'écriture, `supersede`, consolidation, relecture humaine |
| Empoisonnement par contenu piégé | Validation, `verified`, provenance, cadrage du contenu comme donnée |
| Recherche multilingue faible | Modèle d'embeddings multilingue par défaut, recherche hybride |
| Concurrence (Space Teams, projets open source) | Fichiers que le client possède, auto-hébergement, gouvernance et audit |

## 14. Questions ouvertes

- Licence : AGPL pour protéger, ou Apache 2.0 avec une offre commerciale (support, fonctionnalités entreprise) ?
- Modèle économique : open core, support, ou instances gérées ?
- Remote git : imposer un GitLab ou GitHub existant, ou embarquer un serveur git minimal (Gitea) dans le compose pour les entreprises qui n'en ont pas ?
- Modèle d'embeddings par défaut : compromis taille/qualité sur un petit VPS sans GPU
- Disponibilité du nom LoreMd (GitHub, domaine, INPI, EUIPO)
