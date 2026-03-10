"""Tests for the git write strategy module."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path

from markdown_vault_mcp.git import (
    GitWriteStrategy,
    _find_git_root,
    git_write_strategy,
)


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


@pytest.fixture
def git_repo_with_remote(tmp_path: Path) -> tuple[Path, Path]:
    """Create a working repo with a bare remote for push testing."""
    import subprocess

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
    # Initial commit + push so upstream tracking exists.
    (work / "README.md").write_text("# Test\n")
    subprocess.run(
        ["git", "-C", str(work), "add", "."],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["git", "-C", str(work), "commit", "-m", "init"],
        check=True,
        capture_output=True,
    )
    # Detect the default branch name (main or master).
    branch_result = subprocess.run(
        ["git", "-C", str(work), "branch", "--show-current"],
        capture_output=True,
        text=True,
    )
    branch = branch_result.stdout.strip() or "main"
    subprocess.run(
        ["git", "-C", str(work), "push", "-u", "origin", branch],
        check=True,
        capture_output=True,
    )
    return work, bare


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

    def test_no_op_write_skips_commit(self, git_repo: Path) -> None:
        """Writing identical content should not produce an error commit."""
        import subprocess

        callback = git_write_strategy()

        # Create and commit the file.
        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")
        callback(test_file, "# Note\n", "write")

        # Write identical content again — should not error.
        callback(test_file, "# Note\n", "write")

        # Only one write commit should exist (not two).
        result = subprocess.run(
            ["git", "-C", str(git_repo), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert result.stdout.count("write: note.md") == 1

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


class TestGitWriteStrategyClass:
    """Tests for the GitWriteStrategy class directly."""

    def test_flush_pushes_to_remote(
        self, git_repo_with_remote: tuple[Path, Path]
    ) -> None:
        """flush() pushes accumulated commits to the bare remote."""
        import subprocess

        work, bare = git_repo_with_remote

        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        md_file = work / "test.md"
        md_file.write_text("# Test\n")
        strategy(md_file, "# Test\n", "write")

        # Not pushed yet (push_delay_s=0 means push only on close/flush).
        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "write: test.md" not in result.stdout

        # Flush triggers push.
        strategy.flush()

        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "write: test.md" in result.stdout

    def test_close_flushes(self, git_repo_with_remote: tuple[Path, Path]) -> None:
        """close() flushes pending push and marks strategy as closed."""
        import subprocess

        work, bare = git_repo_with_remote

        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        md_file = work / "test.md"
        md_file.write_text("# Test\n")
        strategy(md_file, "# Test\n", "write")

        strategy.close()

        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "write: test.md" in result.stdout

        # Further writes are ignored after close.
        md_file.write_text("# Updated\n")
        strategy(md_file, "# Updated\n", "edit")
        result2 = subprocess.run(
            ["git", "-C", str(work), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "edit: test.md" not in result2.stdout

    def test_deferred_push_fires_after_delay(
        self, git_repo_with_remote: tuple[Path, Path]
    ) -> None:
        """Timer-based push fires after push_delay_s of idle."""
        import subprocess

        work, bare = git_repo_with_remote

        strategy = GitWriteStrategy(token=None, push_delay_s=0.3)
        md_file = work / "test.md"
        md_file.write_text("# Test\n")
        strategy(md_file, "# Test\n", "write")

        # Not pushed immediately.
        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "write: test.md" not in result.stdout

        # Poll until push lands (max 3s).
        for _ in range(30):
            time.sleep(0.1)
            result = subprocess.run(
                ["git", "-C", str(bare), "log", "--oneline"],
                capture_output=True,
                text=True,
            )
            if "write: test.md" in result.stdout:
                break
        else:
            pytest.fail("Deferred push did not fire within 3 seconds")

        strategy.close()

    def test_multiple_writes_single_push(
        self, git_repo_with_remote: tuple[Path, Path]
    ) -> None:
        """Multiple rapid writes result in a single deferred push."""
        import subprocess

        work, bare = git_repo_with_remote

        strategy = GitWriteStrategy(token=None, push_delay_s=0.3)

        for i in range(5):
            md_file = work / f"note_{i}.md"
            md_file.write_text(f"# Note {i}\n")
            strategy(md_file, f"# Note {i}\n", "write")

        # Not pushed yet.
        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "note_4.md" not in result.stdout

        # Poll until push lands (max 3s).
        for _ in range(30):
            time.sleep(0.1)
            result = subprocess.run(
                ["git", "-C", str(bare), "log", "--oneline"],
                capture_output=True,
                text=True,
            )
            if "note_4.md" in result.stdout:
                break
        else:
            pytest.fail("Deferred push did not fire within 3 seconds")

        # All 5 commits pushed in a single push.
        for i in range(5):
            assert f"write: note_{i}.md" in result.stdout

        strategy.close()

    def test_push_with_token_to_bare_remote(
        self, git_repo_with_remote: tuple[Path, Path]
    ) -> None:
        """Push with token uses GIT_ASKPASS against a local bare remote."""
        import subprocess

        work, bare = git_repo_with_remote

        strategy = GitWriteStrategy(token="dummy_token", push_delay_s=0)
        md_file = work / "test.md"
        md_file.write_text("# Test\n")
        strategy(md_file, "# Test\n", "write")
        strategy.flush()

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

        recorded_cmds: list[list[str]] = []
        original_run = subprocess.run

        def recording_run(cmd: list[str], **kwargs):  # type: ignore[no-untyped-def]
            recorded_cmds.append(list(cmd))
            return original_run(cmd, **kwargs)

        # Set up repo with remote inline so we can patch subprocess.
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
        strategy = GitWriteStrategy(token=secret_token, push_delay_s=0)
        md_file = work / "check.md"
        md_file.write_text("# Check\n")

        with patch("markdown_vault_mcp.git.subprocess.run", side_effect=recording_run):
            strategy(md_file, "# Check\n", "write")
            strategy.flush()

        for cmd in recorded_cmds:
            for arg in cmd:
                assert secret_token not in arg, (
                    f"Token found in command argument: {cmd!r}"
                )

    def test_startup_recovery_pushes_unpushed(
        self, git_repo_with_remote: tuple[Path, Path]
    ) -> None:
        """On first invocation, unpushed local commits are pushed."""
        import subprocess

        work, bare = git_repo_with_remote

        # Create a local commit without pushing.
        md_file = work / "local_only.md"
        md_file.write_text("# Local\n")
        subprocess.run(
            ["git", "-C", str(work), "add", "--", str(md_file)],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["git", "-C", str(work), "commit", "-m", "local only"],
            check=True,
            capture_output=True,
        )

        # Verify not on remote.
        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "local only" not in result.stdout

        # Create strategy and trigger first invocation.
        strategy = GitWriteStrategy(token=None, push_delay_s=0)
        md_file2 = work / "trigger.md"
        md_file2.write_text("# Trigger\n")
        strategy(md_file2, "# Trigger\n", "write")
        strategy.flush()

        # Both the old unpushed commit and the new one should be on remote.
        result = subprocess.run(
            ["git", "-C", str(bare), "log", "--oneline"],
            capture_output=True,
            text=True,
        )
        assert "local only" in result.stdout
        assert "write: trigger.md" in result.stdout


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
        assert isinstance(kwargs["on_write"], GitWriteStrategy)

    def test_no_git_token_no_callback(self, tmp_path: Path) -> None:
        """to_collection_kwargs() omits on_write when git_token is None."""
        from markdown_vault_mcp.config import CollectionConfig

        config = CollectionConfig(
            source_dir=tmp_path,
            read_only=False,
        )
        kwargs = config.to_collection_kwargs()
        assert "on_write" not in kwargs

    def test_push_delay_passed_to_strategy(self, tmp_path: Path) -> None:
        """to_collection_kwargs() passes git_push_delay_s to strategy."""
        from markdown_vault_mcp.config import CollectionConfig

        config = CollectionConfig(
            source_dir=tmp_path,
            read_only=False,
            git_token="ghp_test",
            git_push_delay_s=60.0,
        )
        kwargs = config.to_collection_kwargs()
        strategy = kwargs["on_write"]
        assert isinstance(strategy, GitWriteStrategy)
        assert strategy._push_delay_s == 60.0

    def test_load_config_reads_push_delay(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() reads GIT_PUSH_DELAY_S from environment."""
        from markdown_vault_mcp.config import load_config

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S", "45")
        config = load_config()
        assert config.git_push_delay_s == 45.0

    def test_load_config_invalid_push_delay_uses_default(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """load_config() falls back to default on invalid GIT_PUSH_DELAY_S."""
        from markdown_vault_mcp.config import load_config

        monkeypatch.setenv("MARKDOWN_VAULT_MCP_SOURCE_DIR", str(tmp_path))
        monkeypatch.setenv("MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S", "not_a_number")
        config = load_config()
        assert config.git_push_delay_s == 30.0


class TestCollectionCloseWiresStrategy:
    def test_collection_close_calls_strategy_close(self, tmp_path: Path) -> None:
        """Collection.close() calls on_write.close() if available."""
        from markdown_vault_mcp.collection import Collection

        closed = []

        class MockStrategy:
            def __call__(self, path, content, operation):  # type: ignore[no-untyped-def]
                pass

            def close(self) -> None:
                closed.append(True)

        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "test.md").write_text("# Test\n")
        col = Collection(
            source_dir=vault,
            read_only=False,
            on_write=MockStrategy(),  # type: ignore[arg-type]
        )
        col.close()

        assert closed == [True]


class TestCheckIdentity:
    """Tests for the _check_identity() warning path."""

    def test_check_identity_warns_when_no_user_email(
        self, git_repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_check_identity warns when git config has no user.email."""
        import subprocess
        from unittest.mock import patch

        # Remove user.email from the repo config so git config returns empty.
        subprocess.run(
            ["git", "-C", str(git_repo), "config", "--unset", "user.email"],
            capture_output=True,
        )

        strategy = GitWriteStrategy()
        strategy._git_root = git_repo

        # Mock subprocess.run to return empty stdout (no user.email).
        with patch("markdown_vault_mcp.git.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            strategy._check_identity()

        # Verify warning was logged with the expected message.
        assert any(
            "no user.email in git config" in record.message
            for record in caplog.records
            if record.levelname == "WARNING"
        )
        # Verify the default identity is mentioned in the warning.
        assert any(
            "markdown-vault-mcp" in record.message and "noreply@markdown-vault-mcp" in record.message
            for record in caplog.records
            if record.levelname == "WARNING"
        )

    def test_check_identity_no_warning_when_user_email_set(
        self, git_repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_check_identity does not warn when git config has user.email."""
        from unittest.mock import patch

        strategy = GitWriteStrategy()
        strategy._git_root = git_repo

        # Mock subprocess.run to return non-empty stdout (user.email is set).
        with patch("markdown_vault_mcp.git.subprocess.run") as mock_run:
            mock_run.return_value.stdout = "existing@example.com\n"
            strategy._check_identity()

        # Verify no warning was logged.
        assert not any(
            "no user.email in git config" in record.message
            for record in caplog.records
            if record.levelname == "WARNING"
        )

    def test_check_identity_custom_name_and_email_in_warning(
        self, git_repo: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_check_identity warning shows custom commit name and email."""
        from unittest.mock import patch

        strategy = GitWriteStrategy(
            commit_name="CustomBot", commit_email="bot@custom.local"
        )
        strategy._git_root = git_repo

        with patch("markdown_vault_mcp.git.subprocess.run") as mock_run:
            mock_run.return_value.stdout = ""
            strategy._check_identity()

        # Verify the warning mentions the custom identity.
        assert any(
            "CustomBot" in record.message and "bot@custom.local" in record.message
            for record in caplog.records
            if record.levelname == "WARNING"
        )


class TestCommitterIdentityInCommit:
    """Tests that commit_name and commit_email appear in git commit commands."""

    def test_default_committer_in_commit_flags(
        self, git_repo: Path
    ) -> None:
        """_stage_and_commit uses default committer identity in -c flags."""
        import subprocess
        from unittest.mock import patch

        recorded_cmds: list[list[str]] = []
        original_run = subprocess.run

        def recording_run(cmd: list[str], **kwargs):  # type: ignore[no-untyped-def]
            recorded_cmds.append(list(cmd))
            return original_run(cmd, **kwargs)

        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")

        with patch("markdown_vault_mcp.git.subprocess.run", side_effect=recording_run):
            from markdown_vault_mcp.git import _stage_and_commit

            _stage_and_commit(git_repo, test_file, "write")

        # Find the commit command (should have "commit" in it).
        commit_cmd = None
        for cmd in recorded_cmds:
            if "commit" in cmd:
                commit_cmd = cmd
                break

        assert commit_cmd is not None, "No commit command found"
        # Verify the default -c flags are present.
        assert "-c" in commit_cmd
        assert "user.name=markdown-vault-mcp" in commit_cmd
        assert "user.email=noreply@markdown-vault-mcp" in commit_cmd

    def test_custom_committer_in_commit_flags(self, git_repo: Path) -> None:
        """_stage_and_commit uses custom committer identity in -c flags."""
        import subprocess
        from unittest.mock import patch

        recorded_cmds: list[list[str]] = []
        original_run = subprocess.run

        def recording_run(cmd: list[str], **kwargs):  # type: ignore[no-untyped-def]
            recorded_cmds.append(list(cmd))
            return original_run(cmd, **kwargs)

        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")

        with patch("markdown_vault_mcp.git.subprocess.run", side_effect=recording_run):
            from markdown_vault_mcp.git import _stage_and_commit

            _stage_and_commit(
                git_repo,
                test_file,
                "write",
                commit_name="CustomBot",
                commit_email="bot@example.com",
            )

        # Find the commit command.
        commit_cmd = None
        for cmd in recorded_cmds:
            if "commit" in cmd:
                commit_cmd = cmd
                break

        assert commit_cmd is not None
        # Verify the custom -c flags are present.
        assert "-c" in commit_cmd
        assert "user.name=CustomBot" in commit_cmd
        assert "user.email=bot@example.com" in commit_cmd

    def test_strategy_passes_commit_identity_to_stage_and_commit(
        self, git_repo: Path
    ) -> None:
        """GitWriteStrategy passes commit_name and commit_email to _stage_and_commit."""
        from unittest.mock import patch

        recorded_calls: list[tuple] = []

        def recording_stage_and_commit(
            git_root, path, operation, commit_name="default", commit_email="default"
        ):
            recorded_calls.append(
                (git_root, path, operation, commit_name, commit_email)
            )
            # Call original to actually stage/commit
            from markdown_vault_mcp.git import _stage_and_commit as orig

            orig(git_root, path, operation, commit_name, commit_email)

        test_file = git_repo / "note.md"
        test_file.write_text("# Note\n")

        strategy = GitWriteStrategy(
            commit_name="BotName", commit_email="bot@test.local"
        )

        with patch(
            "markdown_vault_mcp.git._stage_and_commit",
            side_effect=recording_stage_and_commit,
        ):
            strategy(test_file, "# Note\n", "write")

        # Verify the custom identity was passed.
        assert len(recorded_calls) > 0
        call = recorded_calls[0]
        assert call[3] == "BotName"  # commit_name
        assert call[4] == "bot@test.local"  # commit_email
