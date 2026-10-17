from __future__ import annotations

from pathlib import Path

import pytest

from loremd.core.git import GitRepo
from loremd.errors import GitError


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    repository = GitRepo(tmp_path / "produit")
    repository.init()
    return repository


def write(repo: GitRepo, relative: str, content: str) -> str:
    path = repo.path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return relative


def test_init_est_idempotent(repo: GitRepo) -> None:
    repo.init()

    assert repo.exists


def test_le_commit_porte_le_nom_de_l_agent(repo: GitRepo) -> None:
    path = write(repo, "decisions/a.md", "première version")

    sha = repo.commit(
        [path], "decision: a", author_name="claude-code", author_email="claude@agents.local"
    )

    assert sha is not None
    commits = repo.history(path)
    assert commits[0].author == "claude-code"
    assert commits[0].message == "decision: a"


def test_commit_sans_changement_ne_cree_rien(repo: GitRepo) -> None:
    path = write(repo, "decisions/a.md", "contenu")
    repo.commit([path], "decision: a", author_name="agent", author_email="a@b.c")

    assert repo.commit([path], "rien", author_name="agent", author_email="a@b.c") is None


def test_history_remonte_les_versions(repo: GitRepo) -> None:
    path = write(repo, "decisions/a.md", "v1")
    repo.commit([path], "decision: a", author_name="agent", author_email="a@b.c")
    write(repo, "decisions/a.md", "v2")
    repo.commit([path], "update: a — correction", author_name="humain", author_email="h@b.c")

    commits = repo.history(path)

    assert [commit.message for commit in commits] == ["update: a — correction", "decision: a"]


def test_changed_files_liste_le_diff_depuis_un_commit(repo: GitRepo) -> None:
    first = write(repo, "decisions/a.md", "v1")
    base = repo.commit([first], "decision: a", author_name="agent", author_email="a@b.c")
    second = write(repo, "notes/b.md", "note")
    (repo.path / "decisions/a.md").unlink()
    repo.commit([second, first], "lot suivant", author_name="agent", author_email="a@b.c")

    changes = dict((path, status) for status, path in repo.changed_files(base))

    assert changes[second] == "A"
    assert changes[first] == "D"


def test_changed_files_sans_base_liste_tout(repo: GitRepo) -> None:
    path = write(repo, "facts/c.md", "fait")
    repo.commit([path], "fact: c", author_name="agent", author_email="a@b.c")

    assert repo.changed_files(None) == [("A", path)]


def test_une_commande_git_en_echec_leve(repo: GitRepo) -> None:
    with pytest.raises(GitError):
        repo.run("checkout", "branche-inexistante")
