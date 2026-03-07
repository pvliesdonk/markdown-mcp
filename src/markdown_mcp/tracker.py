"""Hash-based change detection for markdown-mcp."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path

from markdown_mcp.types import ChangeSet, ParsedNote

logger = logging.getLogger(__name__)


class ChangeTracker:
    """Detects file additions, modifications, and deletions using SHA256 hashes.

    State is persisted as a JSON file mapping relative document paths to their
    last-seen SHA256 hex digest. On the first run (no state file), every file
    on disk is treated as newly added.

    Example::

        tracker = ChangeTracker(Path("/data/vault/.markdown_mcp/state.json"))
        changes = tracker.detect_changes(Path("/data/vault"))
        # process changes ...
        tracker.update_state(notes)
    """

    def __init__(self, state_path: Path) -> None:
        """Initialise the tracker.

        Args:
            state_path: Path to the JSON state file. The file need not exist
                yet; the parent directory is created on first write.
        """
        self._state_path = state_path

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def detect_changes(
        self,
        source_dir: Path,
        glob_pattern: str = "**/*.md",
    ) -> ChangeSet:
        """Compare files on disk against stored state and return the delta.

        Algorithm:

        1. Load existing state (empty dict if state file does not exist).
        2. Scan *source_dir* for all files matching *glob_pattern*.
        3. Compute SHA256 of each file's raw bytes.
        4. Categorise each path:

           - present on disk but absent from state → **added**
           - present in both and hash differs → **modified**
           - present in state but absent from disk → **deleted**
           - present in both and hash matches → **unchanged** (counted only)

        5. Return a :class:`~markdown_mcp.types.ChangeSet`.

        Args:
            source_dir: Root directory of the markdown collection.
            glob_pattern: Glob pattern used to discover files, relative to
                *source_dir*. Defaults to ``"**/*.md"``.

        Returns:
            A :class:`~markdown_mcp.types.ChangeSet` describing the delta.
        """
        stored_state = self._load_state()

        # Build a mapping of relative path → sha256 for current disk contents.
        disk_state: dict[str, str] = {}
        for abs_path in sorted(source_dir.glob(glob_pattern)):
            if not abs_path.is_file():
                continue
            try:
                rel_str = abs_path.relative_to(source_dir).as_posix()
            except ValueError:
                logger.warning("File outside source_dir, skipping: %s", abs_path)
                continue
            try:
                content_hash = self._compute_hash(abs_path)
            except OSError as exc:
                logger.warning("Cannot read %s, skipping: %s", abs_path, exc)
                continue
            disk_state[rel_str] = content_hash

        added: list[str] = []
        modified: list[str] = []
        unchanged: int = 0

        for rel_path, current_hash in disk_state.items():
            if rel_path not in stored_state:
                added.append(rel_path)
            elif stored_state[rel_path] != current_hash:
                modified.append(rel_path)
            else:
                unchanged += 1

        deleted: list[str] = [
            rel_path for rel_path in stored_state if rel_path not in disk_state
        ]

        logger.debug(
            "detect_changes: %d added, %d modified, %d deleted, %d unchanged",
            len(added),
            len(modified),
            len(deleted),
            unchanged,
        )

        return ChangeSet(
            added=added,
            modified=modified,
            deleted=deleted,
            unchanged=unchanged,
        )

    def update_state(self, notes: list[ParsedNote]) -> None:
        """Persist the current hash state derived from *notes*.

        Overwrites the entire state file with the hashes from *notes*. Call
        this after successfully (re)indexing a set of documents to record their
        current content hashes.

        Args:
            notes: Parsed notes whose ``path`` and ``content_hash`` attributes
                form the new state. Any paths not in this list are dropped from
                state (treated as deleted on the next scan).
        """
        new_state = {note.path: note.content_hash for note in notes}
        self._save_state(new_state)
        logger.debug("update_state: wrote state for %d document(s)", len(notes))

    def reset(self) -> None:
        """Delete the state file so the next scan treats all files as added.

        If the state file does not exist, this is a no-op.
        """
        if self._state_path.exists():
            self._state_path.unlink()
            logger.debug("reset: deleted state file %s", self._state_path)
        else:
            logger.debug("reset: state file does not exist, nothing to delete")

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_state(self) -> dict[str, str]:
        """Load the persisted state from disk.

        Returns:
            Mapping of relative document path to SHA256 hex digest.
            Returns an empty dict when the state file does not exist.
        """
        if not self._state_path.exists():
            logger.debug(
                "No state file at %s; treating all files as added", self._state_path
            )
            return {}
        try:
            with self._state_path.open(encoding="utf-8") as fh:
                state = json.load(fh)
            if not isinstance(state, dict):
                logger.warning(
                    "State file %s is malformed (expected object); resetting",
                    self._state_path,
                )
                return {}
            return state
        except (OSError, json.JSONDecodeError) as exc:
            logger.warning(
                "Cannot read state file %s (%s); treating all files as added",
                self._state_path,
                exc,
            )
            return {}

    def _save_state(self, state: dict[str, str]) -> None:
        """Write *state* to the state file as JSON.

        Creates parent directories if they do not exist.

        Args:
            state: Mapping of relative document path to SHA256 hex digest.
        """
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self._state_path.parent, suffix=".tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as fh:
                json.dump(state, fh, indent=2, sort_keys=True)
            Path(tmp_path).replace(self._state_path)
        except:
            Path(tmp_path).unlink(missing_ok=True)
            raise
        logger.debug("Saved state for %d path(s) to %s", len(state), self._state_path)

    def _compute_hash(self, path: Path) -> str:
        """Compute the SHA256 hex digest of *path* using chunked reads.

        Reads the file in 8 KiB chunks so that large files do not require
        loading the entire content into memory at once.

        Args:
            path: Absolute path to the file to hash.

        Returns:
            Lowercase hex-encoded SHA256 digest.

        Raises:
            OSError: If the file cannot be opened or read.
        """
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()
