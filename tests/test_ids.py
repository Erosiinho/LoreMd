from __future__ import annotations

from loremd.ids import is_ulid, memory_filename, new_ulid, slugify


def test_ulid_a_la_bonne_forme() -> None:
    value = new_ulid()

    assert len(value) == 26
    assert is_ulid(value)


def test_les_ulid_sont_ordonnes_par_date() -> None:
    older = new_ulid(1_700_000_000_000)
    newer = new_ulid(1_800_000_000_000)

    assert older < newer


def test_slugify_translittere_et_nettoie() -> None:
    assert slugify("Stack API — Postgres + Drizzle") == "stack-api-postgres-drizzle"
    assert slugify("Préférence : indentation à 4 espaces") == "preference-indentation-a-4-espaces"
    assert slugify("///") == "memoire"


def test_slugify_coupe_sur_un_mot_entier() -> None:
    slug = slugify("un titre vraiment très long qui dépasse la limite imposée", max_length=20)

    assert len(slug) <= 20
    assert not slug.endswith("-")


def test_nom_de_fichier_combine_slug_et_suffixe_d_id() -> None:
    name = memory_filename("Stack API", "01JA8KZ3M9X2QH7R4T6VABCDEF")

    assert name == "stack-api-abcdef.md"
