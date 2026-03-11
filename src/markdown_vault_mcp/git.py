"""Git write strategy for auto-commit and push on write operations.

Provides :class:`GitWriteStrategy`, a stateful callback that commits
per-write and defers pushes to a background timer.  Also retains the
legacy :func:`git_write_strategy` factory for backward compatibility.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shlex
import stat
import subprocess
import tempfile
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Literal

if TYPE_CHECKING:
    from collections.abc import Callable

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


class GitWriteStrategy:
    """Stateful git strategy: commit per write, deferred push.

    On each callback invocation:

    1. Stages the changed file (``git add`` or ``git add -u`` for deletes).
    2. Commits with an auto-generated message (``"operation: path"``).
    3. Resets the push timer — push fires after ``push_delay_s`` of idle.

    Push is deferred to a background ``threading.Timer`` that resets on
    each write.  When the timer fires (no writes for ``push_delay_s``),
    all accumulated local commits are pushed in a single ``git push``.

    On startup, any unpushed local commits (from a previous crash) are
    pushed immediately.

    Args:
        token: PAT for HTTPS push via ``GIT_ASKPASS``.  ``None`` uses
            SSH or pre-configured credentials.
        push_delay_s: Seconds of idle before pushing.  ``0`` disables
            the timer (push only on :meth:`close`).
        commit_name: Git committer name; defaults to
            :attr:`DEFAULT_COMMIT_NAME`.
        commit_email: Git committer email; defaults to
            :attr:`DEFAULT_COMMIT_EMAIL`.
        git_lfs: When ``True`` (default), run ``git lfs pull`` during
            lazy initialisation so LFS pointers are resolved before the
            first write is committed.  Requires ``git-lfs`` to be on
            ``PATH``; failures are logged at ERROR and never propagated.

    Example::

        strategy = GitWriteStrategy(token="ghp_...", push_delay_s=30)
        collection = Collection(on_write=strategy, ...)
        # ... writes happen, push deferred ...
        strategy.close()  # final flush
    """

    #: Default committer name used when none is set in git config or env.
    DEFAULT_COMMIT_NAME = "markdown-vault-mcp"
    #: Default committer email used when none is set in git config or env.
    DEFAULT_COMMIT_EMAIL = "noreply@markdown-vault-mcp"

    def __init__(
        self,
        token: str | None = None,
        push_delay_s: float = 30.0,
        commit_name: str | None = None,
        commit_email: str | None = None,
        git_lfs: bool = True,
    ) -> None:
        self._token = token
        self._push_delay_s = push_delay_s
        self._commit_name = commit_name or self.DEFAULT_COMMIT_NAME
        self._commit_email = commit_email or self.DEFAULT_COMMIT_EMAIL
        self._git_lfs = git_lfs
        self._git_root: Path | None = None
        self._git_root_checked = False
        self._write_init_done = False
        self._push_pending = False
        self._timer: threading.Timer | None = None
        self._lock = threading.Lock()
        self._closed = False
        self._pull_stop = threading.Event()
        self._pull_thread: threading.Thread | None = None
        self._pull_interval_s: int = 0
        self._pull_repo_path: Path | None = None
        self._pause_writes: (
            Callable[[], contextlib.AbstractContextManager[None]] | None
        ) = None
        self._on_pull: Callable[[], None] | None = None

    def _git_env(self) -> dict[str, str] | None:
        """Build environment for git subprocess calls.

        When a token is set, reuse the existing GIT_ASKPASS mechanism to avoid
        prompting interactively. This mirrors the push path and keeps the token
        out of command-line arguments.
        """
        if not self._token:
            return None
        fd, script_path_str = tempfile.mkstemp(suffix=".sh", prefix="git_askpass_")
        script_path = Path(script_path_str)
        os.close(fd)
        # The script echoes the token for any prompt. This matches the existing
        # push behavior and avoids provider-specific username handling.
        script_path.write_text(f"#!/bin/sh\necho {shlex.quote(self._token)}\n")
        script_path.chmod(stat.S_IRWXU)
        # Caller is responsible for unlinking script_path.
        return {
            **os.environ,
            "GIT_ASKPASS": script_path_str,
            "GIT_TERMINAL_PROMPT": "0",
        }

    def _cleanup_git_env(self, env: dict[str, str] | None) -> None:
        if env is None:
            return
        script_path_str = env.get("GIT_ASKPASS")
        if not script_path_str:
            return
        with contextlib.suppress(OSError):
            Path(script_path_str).unlink()

    def _ensure_git_root(self, repo_path: Path) -> Path | None:
        if self._git_root_checked:
            return self._git_root
        with self._lock:
            if not self._git_root_checked:
                self._git_root = _find_git_root(repo_path)
                self._git_root_checked = True
        return self._git_root

    def _ensure_write_init(self) -> None:
        """One-time initialisation for the write path (identity/push/LFS)."""
        if self._write_init_done or self._git_root is None:
            return
        with self._lock:
            if self._write_init_done or self._git_root is None:
                return
            self._check_identity()
            self._push_if_unpushed()
            # LFS pull runs under the git lock to avoid overlapping git ops.
            # Forward auth credentials so token-protected LFS backends
            # authenticate with the same GIT_ASKPASS mechanism used for push.
            env = self._git_env()
            try:
                self._lfs_pull(env=env)
            finally:
                self._cleanup_git_env(env)
            self._write_init_done = True

    def __call__(
        self,
        path: Path,
        content: str,  # noqa: ARG002
        operation: Literal["write", "edit", "delete", "rename"],
    ) -> None:
        """WriteCallback interface: stage + commit, then schedule push."""
        if self._closed:
            return

        self._ensure_git_root(path)
        if self._git_root is None:
            logger.debug(
                "No git repository found for %s; git operations disabled", path
            )
            return

        self._ensure_write_init()

        if self._git_root is None:
            return

        try:
            with self._lock:
                _stage_and_commit(
                    self._git_root,
                    path,
                    operation,
                    commit_name=self._commit_name,
                    commit_email=self._commit_email,
                )
            self._schedule_push()
        except subprocess.CalledProcessError as exc:
            sanitized_stderr = exc.stderr or ""
            if self._token and self._token in sanitized_stderr:
                sanitized_stderr = sanitized_stderr.replace(self._token, "***")
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

    def _schedule_push(self) -> None:
        """Reset the idle push timer."""
        with self._lock:
            self._push_pending = True
            if self._timer is not None:
                self._timer.cancel()
            if self._push_delay_s > 0:
                self._timer = threading.Timer(self._push_delay_s, self._do_push_safe)
                self._timer.daemon = True
                self._timer.start()

    def _do_push_safe(self) -> None:
        """Push wrapper that catches and logs errors."""
        try:
            self._do_push()
        except subprocess.CalledProcessError as exc:
            sanitized_stderr = exc.stderr or ""
            if self._token and self._token in sanitized_stderr:
                sanitized_stderr = sanitized_stderr.replace(self._token, "***")
            logger.error(
                "Git push failed: command %s returned %d\n%s",
                exc.cmd,
                exc.returncode,
                sanitized_stderr,
            )
        except Exception:
            logger.error("Git push failed", exc_info=True)

    def _do_push(self) -> None:
        """Execute git push and clear pending flag.

        Note: ``_push_pending`` is cleared *before* calling ``_push()``.
        If the push fails, commits are not automatically retried — they
        will be pushed on the next write (which resets ``_push_pending``)
        or on the next startup via ``_push_if_unpushed()``.
        """
        with self._lock:
            if not self._push_pending or self._git_root is None:
                return
            self._push_pending = False

        with self._lock:
            _push(self._git_root, self._token)
            logger.info("Git: pushed to remote")

    def _check_identity(self) -> None:
        """Warn once at startup if no git committer identity is configured.

        Runs ``git config user.email`` against the repo.  If it returns
        nothing the repo (and global) git config have no identity set, so
        commits will use the identity supplied to this strategy instance.
        """
        if self._git_root is None:
            return
        try:
            result = subprocess.run(
                ["git", "-C", str(self._git_root), "config", "user.email"],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            return
        if not result.stdout.strip():
            logger.warning(
                "Git: no user.email in git config — commits will use "
                "committer identity '%s <%s>'. Set MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME "
                "and MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL to override.",
                self._commit_name,
                self._commit_email,
            )

    def _push_if_unpushed(self) -> None:
        """On startup, push any local commits ahead of the remote."""
        if self._git_root is None:
            return

        try:
            result = subprocess.run(
                [
                    "git",
                    "-C",
                    str(self._git_root),
                    "log",
                    "--oneline",
                    "@{upstream}..HEAD",
                ],
                capture_output=True,
                text=True,
            )
        except FileNotFoundError:
            logger.debug("Git: git not found, skipping unpushed check")
            return

        if result.returncode != 0:
            # No upstream or no remote — not an error at startup.
            logger.debug("Git: no upstream to check for unpushed commits")
            return

        if result.stdout.strip():
            logger.info("Git: found unpushed commits on startup, pushing now")
            try:
                _push(self._git_root, self._token)
            except subprocess.CalledProcessError as exc:
                sanitized_stderr = exc.stderr or ""
                if self._token and self._token in sanitized_stderr:
                    sanitized_stderr = sanitized_stderr.replace(self._token, "***")
                logger.error(
                    "Git startup push failed: command %s returned %d\n%s",
                    exc.cmd,
                    exc.returncode,
                    sanitized_stderr,
                )

    def _lfs_pull(self, env: dict[str, str] | None = None) -> None:
        """Run ``git lfs pull`` to resolve LFS pointers, if LFS is enabled.

        Called during lazy init and after successful ff-only pull ticks
        (:meth:`sync_once`) so LFS pointer files are resolved before reads,
        indexing, and git commits.
        Failures are logged at ERROR and never propagated to the caller.
        """
        if not self._git_lfs or self._git_root is None:
            return
        try:
            result = subprocess.run(
                ["git", "-C", str(self._git_root), "lfs", "pull"],
                capture_output=True,
                text=True,
                check=True,
                env=env,
            )
            logger.info("Git LFS: pulled from remote")
            if result.stdout.strip():
                logger.debug("Git LFS pull output: %s", result.stdout.strip())
        except subprocess.CalledProcessError as exc:
            logger.error(
                "Git LFS pull failed: command %s returned %d\n%s",
                exc.cmd,
                exc.returncode,
                exc.stderr or "",
            )
        except FileNotFoundError:
            logger.error(
                "Git LFS pull failed: git not found on PATH. "
                "Install git or set MARKDOWN_VAULT_MCP_GIT_LFS=false to suppress this error."
            )

    def sync_once(self, repo_path: Path) -> bool:
        """Fetch + ff-only update once, returning True if HEAD advanced."""
        if self._closed:
            return False

        git_root = self._ensure_git_root(repo_path)
        if git_root is None:
            return False

        env = None
        try:
            env = self._git_env()
            with self._lock:
                upstream_check = subprocess.run(
                    [
                        "git",
                        "-C",
                        str(git_root),
                        "rev-parse",
                        "--verify",
                        "@{upstream}",
                    ],
                    capture_output=True,
                    text=True,
                    env=env,
                )
                if upstream_check.returncode != 0:
                    logger.info("Git pull: no upstream configured; skipping fetch")
                    return False

                old_head = subprocess.run(
                    ["git", "-C", str(git_root), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=env,
                ).stdout.strip()

                subprocess.run(
                    ["git", "-C", str(git_root), "fetch"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=env,
                )

                try:
                    subprocess.run(
                        [
                            "git",
                            "-C",
                            str(git_root),
                            "merge",
                            "--ff-only",
                            "@{upstream}",
                        ],
                        capture_output=True,
                        text=True,
                        check=True,
                        env=env,
                    )
                except subprocess.CalledProcessError as exc:
                    logger.warning(
                        "Git pull: ff-only update failed (diverged?), skipping: %s",
                        (exc.stderr or "").strip(),
                    )
                    return False

                new_head = subprocess.run(
                    ["git", "-C", str(git_root), "rev-parse", "HEAD"],
                    capture_output=True,
                    text=True,
                    check=True,
                    env=env,
                ).stdout.strip()

                # Always attempt LFS pull after a successful fetch+ff-only step.
                self._lfs_pull(env=env)

            return old_head != new_head
        except FileNotFoundError:
            logger.info("Git pull: git not found on PATH; pull loop disabled")
            return False
        except subprocess.CalledProcessError as exc:
            logger.warning(
                "Git pull: git command failed, skipping: %s",
                (exc.stderr or "").strip(),
            )
            return False
        finally:
            self._cleanup_git_env(env)

    def start(
        self,
        *,
        repo_path: Path,
        pull_interval_s: int,
        pause_writes: Callable[[], contextlib.AbstractContextManager[None]]
        | None = None,
        on_pull: Callable[[], None] | None = None,
    ) -> None:
        """Start a periodic fetch + ff-only update loop in a daemon thread."""
        if self._closed or pull_interval_s <= 0:
            return

        git_root = self._ensure_git_root(repo_path)
        if git_root is None:
            return

        # Guard: do not start the loop if there is no upstream configured.
        # This check is intentionally independent of the sync_once() call in
        # sync_from_remote_before_index() — start() may be called even when
        # the startup sync was skipped (pull_interval_s changed at runtime,
        # or Collection.start() called directly by library users).  The double
        # upstream check is harmless (costs one git subprocess) and avoids
        # noisy "no upstream" logs on every tick.
        env = None
        try:
            env = self._git_env()
            upstream_check = subprocess.run(
                ["git", "-C", str(git_root), "rev-parse", "--verify", "@{upstream}"],
                capture_output=True,
                text=True,
                env=env,
            )
            if upstream_check.returncode != 0:
                logger.info("Git pull: no upstream configured; pull loop disabled")
                return
        except FileNotFoundError:
            logger.info("Git pull: git not found on PATH; pull loop disabled")
            return
        finally:
            self._cleanup_git_env(env)

        with self._lock:
            if self._pull_thread is not None and self._pull_thread.is_alive():
                return
            self._pull_repo_path = repo_path
            self._pull_interval_s = pull_interval_s
            self._pause_writes = pause_writes
            self._on_pull = on_pull
            self._pull_stop.clear()
            self._pull_thread = threading.Thread(
                target=self._pull_loop, name="GitPullLoop", daemon=True
            )
            self._pull_thread.start()

    def _pull_loop(self) -> None:
        repo_path = self._pull_repo_path
        if repo_path is None:
            return

        while not self._pull_stop.is_set():
            try:
                did_advance = self.sync_once(repo_path)
                if did_advance and self._on_pull is not None:
                    pause = self._pause_writes
                    if pause is None:
                        self._on_pull()
                    else:
                        with pause():
                            self._on_pull()
            except Exception:
                logger.exception("Git pull loop tick failed")
            # Wait until the next interval, or stop early.
            if self._pull_stop.wait(timeout=self._pull_interval_s):
                break

    def stop(self) -> None:
        """Stop the pull loop thread if it is running."""
        with self._lock:
            thread = self._pull_thread
            if thread is None:
                return
            self._pull_stop.set()
        # Do not block indefinitely on shutdown.
        thread.join(timeout=5.0)
        with self._lock:
            if self._pull_thread is thread:
                self._pull_thread = None

    def flush(self) -> None:
        """Block until any pending push completes.

        Cancels the idle timer and pushes immediately if there are
        pending local commits.
        """
        with self._lock:
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
            pending = self._push_pending

        if pending and self._git_root is not None:
            self._do_push_safe()

    def close(self) -> None:
        """Cancel timer, flush pending push, mark strategy as closed."""
        self._closed = True
        self.stop()
        self.flush()


def git_write_strategy(
    token: str | None = None,
    push_delay_s: float = 0,
    git_lfs: bool = True,
) -> GitWriteStrategy:
    """Create a :class:`GitWriteStrategy` callback.

    Convenience wrapper around :class:`GitWriteStrategy`.  With the
    default ``push_delay_s=0``, commits happen per-write but push only
    fires when :meth:`~GitWriteStrategy.close` or
    :meth:`~GitWriteStrategy.flush` is called.

    When used via :class:`~markdown_vault_mcp.collection.Collection`,
    ``Collection.close()`` automatically calls the strategy's
    ``close()``, so pushes flush on shutdown.  Callers using this
    as a bare ``WriteCallback`` must retain a reference and call
    ``close()`` explicitly.

    .. deprecated::
        Prefer :class:`GitWriteStrategy` directly for access to
        :meth:`~GitWriteStrategy.flush` and :meth:`~GitWriteStrategy.close`.

    .. note::
        The default ``push_delay_s=0`` here differs from
        :class:`GitWriteStrategy`'s default of ``30.0``.  This preserves
        backward compatibility (push on close/flush only).

    Args:
        token: PAT for HTTPS push.
        push_delay_s: Push delay in seconds (default 0 = push on close only).
        git_lfs: When ``True`` (default), run ``git lfs pull`` during init.

    Returns:
        A :class:`GitWriteStrategy` instance (also satisfies
        :data:`~markdown_vault_mcp.types.WriteCallback`).
    """
    return GitWriteStrategy(token=token, push_delay_s=push_delay_s, git_lfs=git_lfs)


def _stage_and_commit(
    git_root: Path,
    path: Path,
    operation: Literal["write", "edit", "delete", "rename"],
    commit_name: str = GitWriteStrategy.DEFAULT_COMMIT_NAME,
    commit_email: str = GitWriteStrategy.DEFAULT_COMMIT_EMAIL,
) -> None:
    """Stage and commit a single file change (no push).

    Args:
        git_root: Git repository root.
        path: Absolute path to the changed file.
        operation: The write operation type.
        commit_name: Git committer name (overrides git config).
        commit_email: Git committer email (overrides git config).
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

    # Skip commit if staging produced no diff (e.g. writing identical content).
    check_result = subprocess.run(
        ["git", "-C", root, "diff", "--cached", "--quiet"],
        capture_output=True,
    )
    if check_result.returncode == 0:
        logger.debug(
            "Git: nothing staged for %s (%s), skipping commit", rel_path, operation
        )
        return

    commit_msg = f"{operation}: {rel_path}"

    subprocess.run(
        [
            "git",
            "-C",
            root,
            "-c",
            f"user.name={commit_name}",
            "-c",
            f"user.email={commit_email}",
            "commit",
            "-m",
            commit_msg,
        ],
        capture_output=True,
        text=True,
        check=True,
    )

    logger.info("Git: committed %s (%s)", rel_path, operation)


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
    root = str(git_root)

    # Always push to "origin".  If the remote is named differently,
    # configure a git remote alias or adjust this constant.
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
