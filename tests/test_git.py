from __future__ import annotations

import subprocess
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


# ----------------------------------------------------------- sync primitives


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(path)], check=True, capture_output=True
    )
    return path


def human_clone(remote: Path, where: Path) -> GitRepo:
    """A human's working copy of the remote, as Obsidian users have."""
    subprocess.run(["git", "clone", str(remote), str(where)], check=True, capture_output=True)
    clone = GitRepo(where)
    clone.run("config", "user.name", "alice")
    clone.run("config", "user.email", "alice@acme.com")
    return clone


def human_commit(clone: GitRepo, relative: str, content: str) -> None:
    write(clone, relative, content)
    clone.commit([relative], f"edit {relative}", author_name="alice", author_email="a@acme.com")


def test_fetch_from_an_empty_remote_reports_no_branch(repo: GitRepo, remote: Path) -> None:
    repo.set_remote(str(remote))

    assert repo.fetch() is False


def test_push_then_fetch_round_trip(repo: GitRepo, remote: Path, tmp_path: Path) -> None:
    repo.set_remote(str(remote))
    path = write(repo, "decisions/a.md", "v1")
    repo.commit([path], "decision: a", author_name="agent", author_email="a@b.c")
    repo.push()

    clone = human_clone(remote, tmp_path / "alice")
    human_commit(clone, "notes/b.md", "from alice")
    clone.push()

    assert repo.fetch() is True
    assert repo.ahead_behind(repo.remote_ref()) == (0, 1)
    repo.fast_forward(repo.remote_ref())
    assert (repo.path / "notes/b.md").read_text() == "from alice"


def test_rebase_replays_local_commits_on_the_remote(
    repo: GitRepo, remote: Path, tmp_path: Path
) -> None:
    repo.set_remote(str(remote))
    base = write(repo, "decisions/a.md", "v1")
    repo.commit([base], "decision: a", author_name="agent", author_email="a@b.c")
    repo.push()
    clone = human_clone(remote, tmp_path / "alice")
    human_commit(clone, "notes/human.md", "human note")
    clone.push()
    local = write(repo, "notes/agent.md", "agent note")
    repo.commit([local], "note: agent", author_name="agent", author_email="a@b.c")

    repo.fetch()
    assert repo.ahead_behind(repo.remote_ref()) == (1, 1)
    assert repo.rebase(repo.remote_ref())

    assert repo.ahead_behind(repo.remote_ref()) == (1, 0)
    assert (repo.path / "notes/human.md").exists()
    # The agent stays the author of its rebased commit.
    assert repo.history("notes/agent.md")[0].author == "agent"


def test_a_conflicting_rebase_is_aborted_and_merge_exposes_both_sides(
    repo: GitRepo, remote: Path, tmp_path: Path
) -> None:
    repo.set_remote(str(remote))
    path = write(repo, "decisions/a.md", "v1\n")
    repo.commit([path], "decision: a", author_name="agent", author_email="a@b.c")
    repo.push()
    clone = human_clone(remote, tmp_path / "alice")
    human_commit(clone, path, "human version\n")
    clone.push()
    write(repo, path, "agent version\n")
    repo.commit([path], "update: a", author_name="agent", author_email="a@b.c")
    head = repo.head()

    repo.fetch()
    assert not repo.rebase(repo.remote_ref())
    # Aborted: the repository is exactly as before.
    assert repo.head() == head
    assert (repo.path / path).read_text() == "agent version\n"

    assert not repo.merge_no_commit(repo.remote_ref())
    assert repo.unmerged_paths() == [path]
    assert repo.stage_content(path, 2) == "agent version\n"
    assert repo.stage_content(path, 3) == "human version\n"

    repo.abort_merge()
    assert repo.unmerged_paths() == []
    assert repo.head() == head


def test_clone_brings_an_existing_space(repo: GitRepo, remote: Path, tmp_path: Path) -> None:
    repo.set_remote(str(remote))
    path = write(repo, "facts/c.md", "fact")
    repo.commit([path], "fact: c", author_name="agent", author_email="a@b.c")
    repo.push()

    copy = GitRepo(tmp_path / "copy")
    copy.clone(str(remote))

    assert (copy.path / path).read_text() == "fact"
    assert copy.current_branch() == "main"


def test_clone_of_an_empty_remote_lands_on_the_default_branch(
    remote: Path, tmp_path: Path
) -> None:
    copy = GitRepo(tmp_path / "copy")
    copy.clone(str(remote))

    assert copy.head() is None
    assert copy.current_branch() == "main"


def test_clone_failure_is_a_git_error(tmp_path: Path) -> None:
    with pytest.raises(GitError):
        GitRepo(tmp_path / "copy").clone(str(tmp_path / "nowhere.git"))
