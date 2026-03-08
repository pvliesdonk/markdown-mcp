"""Tests for the git write strategy module."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.git import _find_git_root, git_write_strategy


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a minimal git repository for testing."""
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(
        ["git", "-C", str(repo), "init"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@test.com"],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test"],
        capture_output=True,
        check=True,
    )
    # Create an initial commit so HEAD exists.
    (repo / "README.md").write_text("# Test\n")
    subprocess.run(
        ["git", "-C", str(repo), "add", "."],
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "init"],
        capture_output=True,
        check=True,
    )
    return repo


class TestFindGitRoot:
    def test_finds_root(self, git_repo: Path) -> None:
        """_find_git_root returns the repo root for a file inside it."""
        subdir = git_repo / "sub"
        subdir.mkdir()
        result = _find_git_root(subdir)
        assert result == git_repo

    def test_no_repo_returns_none(self, tmp_path: Path) -> None:
        """_find_git_root returns None when not in a git repo."""
        isolated = tmp_path / "no_git"
        isolated.mkdir()
        result = _find_git_root(isolated)
        assert result is None


class TestGitWriteStrategy:
    def test_commit_on_write(self, git_repo: Path) -> None:
        """Strategy commits after a write operation."""
        import subprocess

        callback = git_write_strategy()
        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")

        callback(test_file, "# Note\n", "write")

        # Verify commit was created.
        result = subprocess.run(
            ["git", "-C", str(git_repo), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "write: note.md" in result.stdout

    def test_commit_on_edit(self, git_repo: Path) -> None:
        """Strategy commits after an edit operation."""
        import subprocess

        callback = git_write_strategy()

        # First create the file and commit it.
        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")
        callback(test_file, "# Note\n", "write")

        # Now edit it.
        test_file.write_text("# Edited Note\n")
        callback(test_file, "# Edited Note\n", "edit")

        result = subprocess.run(
            ["git", "-C", str(git_repo), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "edit: note.md" in result.stdout

    def test_commit_on_delete(self, git_repo: Path) -> None:
        """Strategy stages deletion after a delete operation."""
        import subprocess

        callback = git_write_strategy()

        # README.md is already tracked. Delete it.
        readme = git_repo / "README.md"
        readme.unlink()

        callback(readme, "", "delete")

        result = subprocess.run(
            ["git", "-C", str(git_repo), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "delete: README.md" in result.stdout

    def test_commit_on_rename(self, git_repo: Path) -> None:
        """Strategy stages both old deletion and new addition on rename."""
        import subprocess

        callback = git_write_strategy()

        # First create and track a file.
        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")
        callback(test_file, "# Note\n", "write")

        # Simulate rename: move file on disk, then call callback with new path.
        new_file = git_repo / "renamed.md"
        test_file.rename(new_file)
        callback(new_file, "# Note\n", "rename")

        result = subprocess.run(
            ["git", "-C", str(git_repo), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "rename: renamed.md" in result.stdout

        # Verify the old file is not left as an unstaged deletion.
        status = subprocess.run(
            ["git", "-C", str(git_repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
        assert status.stdout.strip() == ""

    def test_commit_on_rename_of_untracked_file(self, git_repo: Path) -> None:
        """Rename of a never-committed file: only new path is committed."""
        import subprocess

        callback = git_write_strategy()

        # Create file on disk without going through the callback.
        untracked = git_repo / "untracked.md"
        untracked.write_text("# Untracked\n")

        # Simulate rename: move file, call callback with new path.
        new_file = git_repo / "renamed_untracked.md"
        untracked.rename(new_file)
        callback(new_file, "# Untracked\n", "rename")

        # Commit should succeed; new file is added.
        result = subprocess.run(
            ["git", "-C", str(git_repo), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "rename: renamed_untracked.md" in result.stdout

        # Working tree is clean.
        status = subprocess.run(
            ["git", "-C", str(git_repo), "status", "--porcelain"],
            capture_output=True,
            text=True,
        )
        assert status.stdout.strip() == ""

    def test_no_repo_logs_warning(self, tmp_path: Path) -> None:
        """Strategy logs warning and skips when not in a git repo."""
        isolated = tmp_path / "no_git"
        isolated.mkdir()
        test_file = isolated / "note.md"
        test_file.write_text("# Note\n")

        callback = git_write_strategy()

        # Should not raise, just log a warning.
        callback(test_file, "# Note\n", "write")

    def test_push_failure_does_not_propagate(self, git_repo: Path) -> None:
        """Push failure is logged but does not raise."""
        callback = git_write_strategy()
        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")

        # Push will fail (no remote configured) but should not raise.
        callback(test_file, "# Note\n", "write")

    def test_callback_with_token(self, git_repo: Path) -> None:
        """Strategy accepts a token parameter without error."""
        callback = git_write_strategy(token="ghp_test_token")
        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")

        # Should commit successfully (push will fail — no remote).
        callback(test_file, "# Note\n", "write")

    def test_push_via_askpass_to_bare_remote(self, tmp_path: Path) -> None:
        """Push succeeds via GIT_ASKPASS to a local bare remote."""
        import subprocess

        # Create a bare remote repo.
        bare = tmp_path / "bare.git"
        bare.mkdir()
        subprocess.run(
            ["git", "init", "--bare", str(bare)],
            check=True,
            capture_output=True,
        )

        # Create a working repo with the bare as remote.
        work = tmp_path / "work"
        work.mkdir()
        subprocess.run(
            ["git", "init", str(work)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "config", "user.email", "test@test.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "config", "user.name", "Test"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "remote", "add", "origin", str(bare)],
            check=True,
            capture_output=True,
        )
        # Use push.default=current so first push to bare succeeds without
        # a pre-configured upstream tracking branch.
        subprocess.run(
            ["git", "-C", str(work), "config", "push.default", "current"],
            check=True,
            capture_output=True,
        )

        # Write a file and invoke the callback with a (dummy) token.
        callback = git_write_strategy(token="dummy_token")
        md_file = work / "test.md"
        md_file.write_text("# Test\n")
        callback(md_file, "# Test\n", "write")

        # Verify the push reached the bare remote.
        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "write: test.md" in result.stdout

    def test_token_not_in_command_args(self, tmp_path: Path) -> None:
        """Token must not appear in any git command-line arguments."""
        import subprocess
        from unittest.mock import patch

        # Capture every subprocess.run call and record the cmd args.
        recorded_cmds: list[list[str]] = []
        original_run = subprocess.run

        def recording_run(cmd: list[str], **kwargs):  # type: ignore[no-untyped-def]
            recorded_cmds.append(list(cmd))
            return original_run(cmd, **kwargs)

        # Use git_repo-equivalent setup inline so we can patch subprocess.
        bare = tmp_path / "bare.git"
        bare.mkdir()
        subprocess.run(
            ["git", "init", "--bare", str(bare)],
            check=True,
            capture_output=True,
        )
        work = tmp_path / "work"
        work.mkdir()
        subprocess.run(
            ["git", "init", str(work)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "config", "user.email", "test@test.com"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "config", "user.name", "Test"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "remote", "add", "origin", str(bare)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "config", "push.default", "current"],
            check=True,
            capture_output=True,
        )

        secret_token = "super_secret_pat_xyz"
        callback = git_write_strategy(token=secret_token)
        md_file = work / "check.md"
        md_file.write_text("# Check\n")

        with patch("markdown_vault_mcp.git.subprocess.run", side_effect=recording_run):
            callback(md_file, "# Check\n", "write")

        # Verify the token never appeared in any command argument.
        for cmd in recorded_cmds:
            for arg in cmd:
                assert secret_token not in arg, (
                    f"Token found in command argument: {cmd!r}"
                )


class TestConfigIntegration:
    def test_git_token_wires_up_strategy(
        self,
        tmp_path: Path,
    ) -> None:
        """to_collection_kwargs() includes on_write when git_token is set."""
        from markdown_vault_mcp.config import CollectionConfig

        config = CollectionConfig(
            source_dir=tmp_path,
            read_only=False,
            git_token="ghp_test",
        )
        kwargs = config.to_collection_kwargs()
        assert "on_write" in kwargs
        assert kwargs["on_write"] is not None

    def test_no_git_token_no_callback(self, tmp_path: Path) -> None:
        """to_collection_kwargs() omits on_write when git_token is None."""
        from markdown_vault_mcp.config import CollectionConfig

        config = CollectionConfig(
            source_dir=tmp_path,
            read_only=False,
        )
        kwargs = config.to_collection_kwargs()
        assert "on_write" not in kwargs
