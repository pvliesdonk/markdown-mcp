"""Git write strategy for auto-commit and push on write operations."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from markdown_vault_mcp.types import WriteCallback

logger = logging.getLogger(__name__)


def _find_git_root(path: Path) -> Path | None:
    """Find the git repository root containing *path*.

    Args:
        path: Absolute path to search from.

    Returns:
        The git repository root, or ``None`` if not inside a repo.
    """
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(path if path.is_dir() else path.parent),
                "rev-parse",
                "--show-toplevel",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return Path(result.stdout.strip())
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def git_write_strategy(token: str | None = None) -> WriteCallback:
    """Create an ``on_write`` callback that auto-commits and pushes.

    On each write/edit/delete/rename operation the callback:

    1. Stages the changed file (``git add`` or ``git add -u`` for deletes).
    2. Commits with an auto-generated message (``"operation: relative/path"``).
    3. Pushes to the default remote.

    Git failures are logged at ERROR but **never propagated** — the disk
    write has already succeeded and should not be rolled back.

    Args:
        token: Personal access token for HTTPS push authentication.
            Injected via a temporary ``GIT_ASKPASS`` helper script so the
            token is never present in any process's command-line arguments.
            If ``None``, relies on SSH keys or pre-configured credentials.

    Note:
        The git repository root is discovered on the **first** callback
        invocation and cached for the lifetime of the callback.  If the
        first file is outside any git repository, all subsequent calls
        are also no-ops.  Create a new strategy instance to reset.

    Returns:
        A :data:`~markdown_vault_mcp.types.WriteCallback` suitable for the
        ``on_write`` parameter of
        :class:`~markdown_vault_mcp.collection.Collection`.
    """
    _git_root: Path | None = None
    _checked = False

    def _callback(
        path: Path,
        content: str,  # noqa: ARG001
        operation: Literal["write", "edit", "delete", "rename"],
    ) -> None:
        nonlocal _git_root, _checked

        if not _checked:
            _checked = True
            _git_root = _find_git_root(path)
            if _git_root is None:
                logger.warning(
                    "No git repository found for %s; git operations disabled",
                    path,
                )

        if _git_root is None:
            return

        try:
            _stage_and_push(_git_root, path, operation, token)
        except subprocess.CalledProcessError as exc:
            # Sanitize stderr to avoid leaking PAT tokens in logs.
            # The token is never in exc.cmd (GIT_ASKPASS keeps it out of args),
            # but it could surface in error messages from the remote.
            sanitized_stderr = exc.stderr or ""
            if token and token in sanitized_stderr:
                sanitized_stderr = sanitized_stderr.replace(token, "***")
            logger.error(
                "Git operation failed for %s (%s): command %s returned %d\n%s",
                path,
                operation,
                exc.cmd,
                exc.returncode,
                sanitized_stderr,
            )
        except Exception:
            logger.error(
                "Git operation failed for %s (%s)",
                path,
                operation,
                exc_info=True,
            )

    return _callback


def _stage_and_push(
    git_root: Path,
    path: Path,
    operation: Literal["write", "edit", "delete", "rename"],
    token: str | None,
) -> None:
    """Stage, commit, and push a single file change.

    Args:
        git_root: Git repository root.
        path: Absolute path to the changed file.
        operation: The write operation type.
        token: Optional PAT for HTTPS push.
    """
    root = str(git_root)

    # Stage the change.
    if operation == "delete":
        # File already removed from disk; stage the deletion.
        subprocess.run(
            ["git", "-C", root, "add", "-u", "--", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
    elif operation == "rename":
        # For rename, the old file has been moved on disk.  Stage tracked
        # deletions (-u) to capture the old path removal, then add the new
        # file explicitly.
        # NOTE: ``git add -u`` without a pathspec stages ALL tracked
        # modifications/deletions repo-wide.  In a vault with other
        # uncommitted edits, this may sweep unrelated changes into the
        # auto-commit.  Additionally, if the old file was never committed
        # to git (e.g. written directly by Obsidian and not via this
        # callback), ``git add -u`` will not record its deletion at all —
        # the commit will only add the new path.
        # A future improvement would extend the callback signature to
        # pass both old and new paths, enabling scoped staging.
        subprocess.run(
            ["git", "-C", root, "add", "-u"],
            capture_output=True,
            text=True,
            check=True,
        )
        subprocess.run(
            ["git", "-C", root, "add", "--", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )
    else:
        subprocess.run(
            ["git", "-C", root, "add", "--", str(path)],
            capture_output=True,
            text=True,
            check=True,
        )

    # Generate commit message from operation and relative path.
    try:
        rel_path = path.relative_to(git_root)
    except ValueError:
        rel_path = path
    commit_msg = f"{operation}: {rel_path}"

    subprocess.run(
        ["git", "-C", root, "commit", "-m", commit_msg],
        capture_output=True,
        text=True,
        check=True,
    )

    # Push to remote.
    _push(git_root, token)
    logger.info("Git: committed and pushed %s (%s)", rel_path, operation)


def _push(git_root: Path, token: str | None) -> None:
    """Push to the default remote, using GIT_ASKPASS for token auth.

    When a token is supplied a temporary helper script is written to a
    private temporary file (mode 0o700).  Git reads credentials from this
    script via ``GIT_ASKPASS`` so the token is never present in any
    process's command-line arguments and is therefore not visible in
    ``/proc/<pid>/cmdline``.  The script is deleted in a ``finally`` block
    regardless of push outcome.

    Args:
        git_root: Git repository root.
        token: Optional PAT for HTTPS push.  If ``None``, relies on SSH
            keys or pre-configured git credentials.
    """
    import contextlib
    import stat
    import tempfile

    root = str(git_root)

    if not token:
        subprocess.run(
            ["git", "-C", root, "push", "origin"],
            capture_output=True,
            text=True,
            check=True,
        )
        return

    fd, script_path_str = tempfile.mkstemp(suffix=".sh", prefix="git_askpass_")
    script_path = Path(script_path_str)
    try:
        import shlex

        with os.fdopen(fd, "w") as f:
            f.write(f"#!/bin/sh\necho {shlex.quote(token)}\n")
        script_path.chmod(stat.S_IRWXU)  # 0o700 — owner-only rwx

        env = {
            **os.environ,
            "GIT_ASKPASS": script_path_str,
            "GIT_TERMINAL_PROMPT": "0",
        }
        subprocess.run(
            ["git", "-C", root, "push", "origin"],
            capture_output=True,
            text=True,
            check=True,
            env=env,
        )
    finally:
        with contextlib.suppress(OSError):
            script_path.unlink()
