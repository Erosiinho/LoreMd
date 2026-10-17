# Ouvrir un espace dans Obsidian

Un espace est un dépôt git ordinaire. Un humain le clone et l'ouvre comme un
coffre Obsidian.

```bash
git clone git@gitlab.acme.com:memoire/produit.git ~/Obsidian/produit
```

Dans Obsidian : *Ouvrir un dossier comme coffre* → `~/Obsidian/produit`.

## Ce qu'on peut faire

- Lire, corriger, enrichir les mémoires. Le frontmatter s'affiche comme
  propriétés : `type`, `project`, `tags`, `status`, `verified`.
- Passer `verified: true` sur une mémoire relue : la provenance renvoyée aux
  agents le signalera.
- Passer `status: rejected` sur une mémoire fausse plutôt que de la supprimer —
  l'historique reste lisible.
- Créer une mémoire à la main : la ranger dans le bon dossier, avec au minimum
  `id`, `type`, `title`, `created` et `updated`. Un `id` ULID se génère avec
  `python -c "from loremd.ids import new_ulid; print(new_ulid())"`.

## Comment les changements reviennent dans LoreMd

```bash
git add . && git commit -m "relecture des décisions produit" && git push
```

Le serveur récupère les commits (`loremd space sync`, pull périodique ou
webhook) et réindexe uniquement les fichiers du diff.

En cas de conflit avec une écriture d'agent, LoreMd rebase. Si le rebase échoue,
les deux versions sont conservées et la note passe en `status: conflict` pour
être arbitrée.

## À ne pas faire

- Ne pas renommer les fichiers sans raison : le nom contient le suffixe d'ID.
- Ne pas modifier le champ `id`, qui lie le fichier à l'index.
- Ne pas committer de secret : un hook côté remote le refusera.
