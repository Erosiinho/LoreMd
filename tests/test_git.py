from __future__ import annotations

from pathlib import Path

import pytest

from loremd.core.git import GitRepo
from loremd.errors import GitError


@pytest.fixture
def repo(tmp_path: Path) -> GitRepo:
    repository = GitRepo(tmp_path / "product")
    repository.init()
    return repository


def write(repo: GitRepo, relative: str, content: str) -> str:
    path = repo.path / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return relative


def test_init_is_idempotent(repo: GitRepo) -> None:
    repo.init()

    assert repo.exists


def test_commit_carries_the_agent_name(repo: GitRepo) -> None:
    path = write(repo, "decisions/a.md", "first version")

    sha = repo.commit(
        [path], "decision: a", author_name="claude-code", author_email="claude@agents.local"
    )

    assert sha is not None
    commits = repo.history(path)
    assert commits[0].author == "claude-code"
    assert commits[0].email == "claude@agents.local"
    assert commits[0].message == "decision: a"


def test_commit_author_beats_an_ambient_git_identity(
    repo: GitRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A VPS or a CI runner can export these; the agent must still sign.
    monkeypatch.setenv("GIT_AUTHOR_NAME", "Someone Else")
    monkeypatch.setenv("GIT_AUTHOR_EMAIL", "someone@else.invalid")
    path = write(repo, "decisions/a.md", "first version")

    repo.commit(
        [path], "decision: a", author_name="codex-ci", author_email="codex-ci@agents.local"
    )

    commit = repo.history(path)[0]
    assert commit.author == "codex-ci"
    assert commit.email == "codex-ci@agents.local"


def test_blank_author_falls_back_to_loremd(repo: GitRepo) -> None:
    path = write(repo, "notes/a.md", "note")

    repo.commit([path], "note: a", author_name="  ", author_email="")

    assert repo.history(path)[0].author == "loremd"


def test_commit_without_changes_creates_nothing(repo: GitRepo) -> None:
    path = write(repo, "decisions/a.md", "content")
    repo.commit([path], "decision: a", author_name="agent", author_email="a@b.c")

    assert repo.commit([path], "nothing", author_name="agent", author_email="a@b.c") is None


def test_history_lists_versions(repo: GitRepo) -> None:
    path = write(repo, "decisions/a.md", "v1")
    repo.commit([path], "decision: a", author_name="agent", author_email="a@b.c")
    write(repo, "decisions/a.md", "v2")
    repo.commit([path], "update: a — fix", author_name="human", author_email="h@b.c")

    commits = repo.history(path)

    assert [commit.message for commit in commits] == ["update: a — fix", "decision: a"]


def test_changed_files_lists_the_diff_since_a_commit(repo: GitRepo) -> None:
    first = write(repo, "decisions/a.md", "v1")
    base = repo.commit([first], "decision: a", author_name="agent", author_email="a@b.c")
    second = write(repo, "notes/b.md", "note")
    (repo.path / "decisions/a.md").unlink()
    repo.commit([second, first], "next batch", author_name="agent", author_email="a@b.c")

    changes = dict((path, status) for status, path in repo.changed_files(base))

    assert changes[second] == "A"
    assert changes[first] == "D"


def test_changed_files_without_base_lists_everything(repo: GitRepo) -> None:
    path = write(repo, "facts/c.md", "fact")
    repo.commit([path], "fact: c", author_name="agent", author_email="a@b.c")

    assert repo.changed_files(None) == [("A", path)]


def test_has_commit_distinguishes_known_from_vanished(repo: GitRepo) -> None:
    path = write(repo, "facts/c.md", "fact")
    sha = repo.commit([path], "fact: c", author_name="agent", author_email="a@b.c")

    assert sha is not None
    assert repo.has_commit(sha)
    assert not repo.has_commit("0" * 40)
    assert not repo.has_commit("")


def test_failing_git_command_raises(repo: GitRepo) -> None:
    with pytest.raises(GitError):
        repo.run("checkout", "missing-branch")
