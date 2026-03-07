"""Unit tests for ChangeTracker (tracker.py)."""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

from markdown_mcp.tracker import ChangeTracker
from markdown_mcp.types import Chunk, ParsedNote

if TYPE_CHECKING:
    from pathlib import Path

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_md(directory: Path, name: str, content: str = "# Hello\n") -> Path:
    """Write a markdown file and return its path.

    Args:
        directory: Directory in which to create the file.
        name: Filename (relative to *directory*).
        content: File content. Defaults to a minimal markdown heading.

    Returns:
        Absolute path of the created file.
    """
    path = directory / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_note(path: str, content_hash: str) -> ParsedNote:
    """Create a minimal ParsedNote with the given path and hash.

    Args:
        path: Relative document path used as the state key.
        content_hash: SHA256 hex digest string.

    Returns:
        A :class:`ParsedNote` with one placeholder chunk.
    """
    return ParsedNote(
        path=path,
        frontmatter={},
        title="Title",
        chunks=[Chunk(heading=None, heading_level=0, content="body", start_line=0)],
        content_hash=content_hash,
        modified_at=1000.0,
    )


# ===========================================================================
# Tests
# ===========================================================================


class TestFreshScan:
    def test_fresh_scan_all_added(self, tmp_path: Path) -> None:
        """On first run (no state file) every discovered file is in added."""
        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "a.md")
        _write_md(vault, "b.md")
        _write_md(vault, "sub/c.md")

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)
        changes = tracker.detect_changes(vault)

        assert set(changes.added) == {"a.md", "b.md", "sub/c.md"}
        assert changes.modified == []
        assert changes.deleted == []
        assert changes.unchanged == 0


class TestModifiedFile:
    def test_modified_file_detected(self, tmp_path: Path) -> None:
        """A file whose content changes between scans appears in modified."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "note.md", "original content\n")

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)

        tracker.detect_changes(vault)
        note = _make_note("note.md", hashlib.sha256(md.read_bytes()).hexdigest())
        tracker.update_state([note])

        # Modify the file.
        md.write_text("modified content\n", encoding="utf-8")

        changes2 = tracker.detect_changes(vault)
        assert "note.md" in changes2.modified
        assert "note.md" not in changes2.added
        assert changes2.deleted == []


class TestDeletedFile:
    def test_deleted_file_detected(self, tmp_path: Path) -> None:
        """A file present in state but gone from disk appears in deleted."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "vanish.md")

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)

        tracker.detect_changes(vault)
        note = _make_note("vanish.md", hashlib.sha256(md.read_bytes()).hexdigest())
        tracker.update_state([note])

        md.unlink()

        changes2 = tracker.detect_changes(vault)
        assert "vanish.md" in changes2.deleted
        assert "vanish.md" not in changes2.added
        assert "vanish.md" not in changes2.modified


class TestUnchangedFiles:
    def test_unchanged_files_counted(self, tmp_path: Path) -> None:
        """Files with matching hashes contribute to the unchanged count."""
        vault = tmp_path / "vault"
        vault.mkdir()
        files = ["x.md", "y.md", "z.md"]
        mds = [_write_md(vault, name) for name in files]

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)
        tracker.detect_changes(vault)

        notes = [
            _make_note(name, hashlib.sha256(md.read_bytes()).hexdigest())
            for name, md in zip(files, mds, strict=True)
        ]
        tracker.update_state(notes)

        changes2 = tracker.detect_changes(vault)
        assert changes2.added == []
        assert changes2.modified == []
        assert changes2.deleted == []
        assert changes2.unchanged == len(files)


class TestUpdateStatePersists:
    def test_update_state_persists_state_file(self, tmp_path: Path) -> None:
        """update_state() writes a file that survives between tracker instances."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "persist.md")

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)

        note = _make_note("persist.md", hashlib.sha256(md.read_bytes()).hexdigest())
        tracker.update_state([note])

        assert state_path.exists()

        # A fresh tracker reading the same state file sees no additions.
        tracker2 = ChangeTracker(state_path)
        changes = tracker2.detect_changes(vault)
        assert changes.added == []
        assert changes.unchanged == 1


class TestReset:
    def test_reset_clears_state(self, tmp_path: Path) -> None:
        """reset() removes the state file; next scan treats all files as added."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "reset.md")

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)

        note = _make_note("reset.md", hashlib.sha256(md.read_bytes()).hexdigest())
        tracker.update_state([note])
        assert state_path.exists()

        tracker.reset()
        assert not state_path.exists()

        changes = tracker.detect_changes(vault)
        assert "reset.md" in changes.added
        assert changes.unchanged == 0


class TestStateFileParentDirs:
    def test_state_file_parent_dirs_created(self, tmp_path: Path) -> None:
        """update_state() creates missing parent directories for the state file."""
        vault = tmp_path / "vault"
        vault.mkdir()
        _write_md(vault, "doc.md")

        deep_state = tmp_path / "a" / "b" / "c" / "state.json"
        assert not deep_state.parent.exists()

        tracker = ChangeTracker(deep_state)
        tracker.update_state([])

        assert deep_state.exists()


class TestStateFileFormat:
    def test_state_file_format_is_path_to_hash(self, tmp_path: Path) -> None:
        """The JSON state file maps relative path strings to SHA256 hex strings."""
        vault = tmp_path / "vault"
        vault.mkdir()
        md = _write_md(vault, "check.md", "some content\n")
        expected_hash = hashlib.sha256(md.read_bytes()).hexdigest()

        state_path = tmp_path / "state.json"
        tracker = ChangeTracker(state_path)
        note = _make_note("check.md", expected_hash)
        tracker.update_state([note])

        raw = json.loads(state_path.read_text(encoding="utf-8"))
        assert isinstance(raw, dict)
        assert "check.md" in raw
        assert raw["check.md"] == expected_hash
        # All values should look like SHA256 hex digests (64 hex chars).
        for value in raw.values():
            assert isinstance(value, str)
            assert len(value) == 64
            int(value, 16)  # raises ValueError if not hex
