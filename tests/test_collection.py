"""Integration tests for Collection."""

from __future__ import annotations

import concurrent.futures
import json
import logging
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentExistsError,
    DocumentNotFoundError,
    EditConflictError,
    ReadOnlyError,
)
from markdown_vault_mcp.hashing import compute_file_hash
from markdown_vault_mcp.types import (
    AttachmentContent,
    AttachmentInfo,
    CollectionStats,
    DeleteResult,
    EditResult,
    NoteContent,
    NoteInfo,
    RenameResult,
    WriteResult,
)

if TYPE_CHECKING:
    from pathlib import Path

    from .conftest import MockEmbeddingProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_collection(
    source_dir: Path,
    *,
    index_path: Path | None = None,
    embeddings_path: Path | None = None,
    embedding_provider: MockEmbeddingProvider | None = None,
    indexed_frontmatter_fields: list[str] | None = None,
    state_path: Path | None = None,
    read_only: bool = True,
    on_write: object = None,
) -> Collection:
    """Create a Collection for testing with sensible defaults.

    Uses an in-memory SQLite index unless *index_path* is given.

    Args:
        source_dir: Root directory of the markdown collection.
        index_path: Optional path to a persistent SQLite file.
        embeddings_path: Base path for vector sidecar files.
        embedding_provider: Provider for semantic search.
        indexed_frontmatter_fields: Fields to index in document_tags.
        state_path: Path for the change-tracker state file.
        read_only: When True, write operations raise ReadOnlyError.
        on_write: Optional callback for write operations.

    Returns:
        A configured :class:`Collection` instance.
    """
    return Collection(
        source_dir=source_dir,
        index_path=index_path,
        embeddings_path=embeddings_path,
        embedding_provider=embedding_provider,
        indexed_frontmatter_fields=indexed_frontmatter_fields,
        state_path=state_path,
        read_only=read_only,
        on_write=on_write,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def collection(vault_path: Path) -> Collection:
    """Built Collection backed by the clean vault fixture."""
    col = _make_collection(vault_path)
    col.build_index()
    return col


# ---------------------------------------------------------------------------
# Build / indexing tests
# ---------------------------------------------------------------------------


class TestBuildIndex:
    def test_build_index_from_fixtures(self, vault_path: Path) -> None:
        """build_index() indexes all parseable documents and their chunks."""
        col = _make_collection(vault_path)
        stats = col.build_index()

        # 9 valid .md files (excludes invalid_utf8.md; malformed_yaml.md skipped).
        assert stats.documents_indexed == 9
        # All fixtures are short (<= 30 lines) → 1 chunk each.
        assert stats.chunks_indexed == 9
        assert stats.skipped >= 0

    def test_build_index_force_rebuild(self, vault_path: Path) -> None:
        """build_index(force=True) rebuilds without crashing."""
        col = _make_collection(vault_path)
        col.build_index()
        stats = col.build_index(force=True)

        assert stats.documents_indexed == 9
        assert stats.chunks_indexed == 9

    def test_build_index_idempotent_without_force(self, vault_path: Path) -> None:
        """Calling build_index() twice (no force) does not double-index."""
        col = _make_collection(vault_path)
        col.build_index()
        # Second call is a no-op; documents_indexed reflects existing count.
        stats2 = col.build_index()
        assert stats2.documents_indexed == 9

    def test_reindex_detects_new_file(self, tmp_path: Path, vault_path: Path) -> None:
        """reindex() detects and indexes a file added after build_index()."""
        state_path = tmp_path / "state.json"
        col = _make_collection(vault_path, state_path=state_path)
        col.build_index()

        # Add a new file to the vault.
        new_file = vault_path / "added_note.md"
        new_file.write_text("# Added Note\n\nThis was added after initial index.\n")

        result = col.reindex()

        assert result.added >= 1

    def test_build_index_continues_on_upsert_error(self, vault_path: Path) -> None:
        """build_index() skips documents that fail to index and continues."""
        col = _make_collection(vault_path)
        call_count = 0
        original_upsert = col._fts.upsert_note

        def upsert_that_fails_once(note):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TypeError("simulated serialization error")
            return original_upsert(note)

        with patch.object(col._fts, "upsert_note", side_effect=upsert_that_fails_once):
            stats = col.build_index()

        # One document errored, remaining 8 indexed successfully.
        assert stats.documents_indexed == 8
        assert stats.chunks_indexed == 8

    def test_reindex_continues_on_upsert_error(
        self, tmp_path: Path, vault_path: Path
    ) -> None:
        """reindex() skips documents that fail to upsert and continues."""
        state_path = tmp_path / "state.json"
        col = _make_collection(vault_path, state_path=state_path)
        col.build_index()

        # Add two new files.
        (vault_path / "new_a.md").write_text("# A\n\nContent A.\n")
        (vault_path / "new_b.md").write_text("# B\n\nContent B.\n")

        call_count = 0
        original_upsert = col._fts.upsert_note

        def upsert_that_fails_once(note):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise TypeError("simulated serialization error")
            return original_upsert(note)

        with patch.object(col._fts, "upsert_note", side_effect=upsert_that_fails_once):
            result = col.reindex()

        # One of the two added files failed, the other succeeded.
        assert result.added == 1


# ---------------------------------------------------------------------------
# Lazy initialisation
# ---------------------------------------------------------------------------


class TestLazyInitialisation:
    def test_search_without_build_index(self, vault_path: Path) -> None:
        """search() triggers lazy initialisation without an explicit build_index()."""
        col = _make_collection(vault_path)

        # Do NOT call build_index() — search() should initialise automatically.
        results = col.search("simple")

        # Either finds something or returns [] — the key is it does not crash.
        assert isinstance(results, list)

    def test_list_without_build_index(self, vault_path: Path) -> None:
        """list() triggers lazy initialisation without an explicit build_index()."""
        col = _make_collection(vault_path)

        notes = col.list()

        assert isinstance(notes, list)
        assert len(notes) == 9


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------


class TestSearch:
    def test_search_keyword_returns_results(self, collection: Collection) -> None:
        """Keyword search for a term present in fixtures returns results."""
        results = collection.search("simple", mode="keyword")

        assert len(results) > 0
        assert all(hasattr(r, "path") for r in results)
        assert all(hasattr(r, "score") for r in results)

    def test_search_keyword_term_in_content(self, collection: Collection) -> None:
        """Keyword results reference documents that contain the query term."""
        results = collection.search("unicode", mode="keyword")

        paths = [r.path for r in results]
        assert any("unicode" in p.lower() for p in paths)

    def test_search_semantic_no_embeddings_raises(self, vault_path: Path) -> None:
        """Semantic search without a provider configured raises ValueError."""
        col = _make_collection(vault_path)
        col.build_index()

        with pytest.raises(ValueError, match="embedding_provider"):
            col.search("any query", mode="semantic")

    def test_search_hybrid_with_mock_embeddings(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """Hybrid search returns results when both FTS and vector indexes are built."""
        embeddings_path = tmp_path / "embeddings"
        col = Collection(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.build_index()
        col.build_embeddings()

        results = col.search("document content", mode="hybrid")

        assert isinstance(results, list)
        # At least some results should come back (9 docs indexed).
        assert len(results) > 0

    def test_rrf_neither_dominates(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """Hybrid search combines FTS and semantic results via RRF.

        Regression: verifies results contain items from both retrieval paths
        rather than one source completely overshadowing the other.
        """
        embeddings_path = tmp_path / "embeddings"
        col = Collection(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.build_index()
        col.build_embeddings()

        # Use a broad query that FTS can handle and semantic can also rank.
        results = col.search("note document", mode="hybrid", limit=9)

        # With 9 documents indexed we expect a meaningful set of results.
        assert len(results) >= 1
        # RRF scores must be positive.
        assert all(r.score > 0 for r in results)

    def test_search_hybrid_no_embeddings_raises(self, vault_path: Path) -> None:
        """Hybrid search without a provider configured raises ValueError."""
        col = _make_collection(vault_path)
        col.build_index()

        with pytest.raises(ValueError, match="embedding_provider"):
            col.search("query", mode="hybrid")

    def test_document_identity_different_folders(
        self,
        tmp_path: Path,
    ) -> None:
        """Same filename in different folders produces distinct search results.

        Regression: document identity is the full relative path, not just
        the filename. Two files named ``note.md`` in different folders must
        be indexed and retrieved as separate documents.
        """
        vault = tmp_path / "dual_vault"
        (vault / "alpha").mkdir(parents=True)
        (vault / "beta").mkdir(parents=True)
        (vault / "alpha" / "note.md").write_text(
            "# Alpha Note\n\nContent from the alpha folder.\n"
        )
        (vault / "beta" / "note.md").write_text(
            "# Beta Note\n\nContent from the beta folder.\n"
        )

        col = _make_collection(vault)
        col.build_index()

        notes = col.list()
        paths = [n.path for n in notes]
        assert "alpha/note.md" in paths
        assert "beta/note.md" in paths
        assert len(paths) == 2

        # Keyword search should find both as separate results.
        results = col.search("note", mode="keyword", limit=10)
        result_paths = [r.path for r in results]
        assert "alpha/note.md" in result_paths
        assert "beta/note.md" in result_paths


# ---------------------------------------------------------------------------
# Read tests
# ---------------------------------------------------------------------------


class TestRead:
    def test_read_returns_content(self, collection: Collection) -> None:
        """read() returns a NoteContent with correct fields."""
        result = collection.read("full_frontmatter.md")

        assert isinstance(result, NoteContent)
        assert result.path == "full_frontmatter.md"
        assert result.title == "Full Frontmatter Note"
        assert "Full Frontmatter Note" in result.content
        assert result.frontmatter.get("cluster") == "fiction"
        assert result.folder == ""

    def test_read_subfolder_document(self, collection: Collection) -> None:
        """read() works for documents nested in subfolders."""
        result = collection.read("subfolder/nested.md")

        assert isinstance(result, NoteContent)
        assert result.path == "subfolder/nested.md"
        assert result.folder == "subfolder"

    def test_read_not_found_returns_none(self, collection: Collection) -> None:
        """read() returns None for a path that does not exist."""
        result = collection.read("nonexistent/missing.md")

        assert result is None

    def test_read_returns_etag(self, collection: Collection, vault_path: Path) -> None:
        """read() returns an etag field containing the SHA256 hex digest."""
        import hashlib

        result = collection.read("full_frontmatter.md")

        assert result is not None
        expected = hashlib.sha256(
            (vault_path / "full_frontmatter.md").read_bytes()
        ).hexdigest()
        assert result.etag == expected

    def test_read_etag_is_stable(self, collection: Collection) -> None:
        """read() returns the same etag on repeated reads of unchanged content."""
        result1 = collection.read("full_frontmatter.md")
        result2 = collection.read("full_frontmatter.md")

        assert result1 is not None
        assert result2 is not None
        assert result1.etag is not None
        assert result1.etag == result2.etag
        assert len(result1.etag) == 64  # SHA256 hex is 64 chars

    def test_read_etag_changes_after_write(self, vault_path: Path) -> None:
        """read() etag changes when file content changes."""
        col = _make_collection(vault_path, read_only=False)
        col.build_index()

        result_before = col.read("full_frontmatter.md")
        assert result_before is not None
        etag_before = result_before.etag

        col.write("full_frontmatter.md", "# Updated\n\nNew content.\n")

        result_after = col.read("full_frontmatter.md")
        assert result_after is not None
        assert result_after.etag != etag_before


# ---------------------------------------------------------------------------
# List tests
# ---------------------------------------------------------------------------


class TestList:
    def test_list_all(self, collection: Collection) -> None:
        """list() returns all indexed documents."""
        notes = collection.list()

        assert len(notes) == 9
        assert all(isinstance(n, NoteInfo) for n in notes)

    def test_list_with_folder(self, collection: Collection) -> None:
        """list(folder=...) returns only documents in that folder."""
        notes = collection.list(folder="subfolder")

        assert len(notes) >= 1
        assert all("subfolder" in n.folder for n in notes)

    def test_list_with_pattern(self, collection: Collection) -> None:
        """list(pattern=...) returns only documents matching the glob.

        ``fnmatch`` treats ``*`` as matching path separators, so
        ``subfolder/*.md`` also matches ``subfolder/deep/doc.md``.
        """
        notes = collection.list(pattern="subfolder/*.md")

        paths = [n.path for n in notes]
        assert "subfolder/nested.md" in paths
        # fnmatch '*' matches across directory separators.
        assert "subfolder/deep/doc.md" in paths
        # Root-level documents must not be included.
        assert all(n.path.startswith("subfolder/") for n in notes)

    def test_list_subfolder_deep_pattern(self, collection: Collection) -> None:
        """list() with a deep glob pattern returns deeply nested documents."""
        notes = collection.list(pattern="subfolder/**/*.md")

        paths = [n.path for n in notes]
        assert "subfolder/deep/doc.md" in paths


# ---------------------------------------------------------------------------
# Metadata / stats
# ---------------------------------------------------------------------------


class TestStats:
    def test_stats_returns_collection_stats(self, collection: Collection) -> None:
        """stats() returns a CollectionStats with correct counts."""
        s = collection.stats()

        assert isinstance(s, CollectionStats)
        assert s.document_count == 9
        assert s.chunk_count == 9
        # Folders: "" (root), "subfolder", "subfolder/deep"
        assert s.folder_count == 3
        assert s.semantic_search_available is False

    def test_stats_semantic_available_when_configured(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """stats() reports semantic_search_available=True when provider is set."""
        col = Collection(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
        )
        col.build_index()

        s = col.stats()
        assert s.semantic_search_available is True

    def test_list_folders(self, collection: Collection) -> None:
        """list_folders() returns the distinct folder values across the index."""
        folders = collection.list_folders()

        assert isinstance(folders, list)
        assert "" in folders  # root documents
        assert "subfolder" in folders
        assert "subfolder/deep" in folders

    def test_list_tags(self, vault_path: Path) -> None:
        """list_tags() returns distinct values for indexed frontmatter fields."""
        col = Collection(
            source_dir=vault_path,
            indexed_frontmatter_fields=["cluster", "topics"],
        )
        col.build_index()

        clusters = col.list_tags("cluster")
        # full_frontmatter.md has cluster: fiction
        assert "fiction" in clusters

        topics = col.list_tags("topics")
        # full_frontmatter.md has topics: [horror, gothic]
        assert "horror" in topics
        assert "gothic" in topics

    def test_list_tags_unindexed_field_returns_empty(
        self, collection: Collection
    ) -> None:
        """list_tags() on a field not in indexed_frontmatter_fields returns []."""
        result = collection.list_tags("cluster")
        assert result == []


# ---------------------------------------------------------------------------
# Write / read-only guard
# ---------------------------------------------------------------------------


class TestWriteReadOnly:
    def test_write_raises_readonly(self, collection: Collection) -> None:
        """write() raises ReadOnlyError on a default (read-only) collection."""
        with pytest.raises(ReadOnlyError):
            collection.write("new_note.md", "# New Note\n\nContent.")

    def test_edit_raises_readonly(self, collection: Collection) -> None:
        """edit() raises ReadOnlyError on a read-only collection."""
        with pytest.raises(ReadOnlyError):
            collection.edit("simple.md", "old text", "new text")

    def test_delete_raises_readonly(self, collection: Collection) -> None:
        """delete() raises ReadOnlyError on a read-only collection."""
        with pytest.raises(ReadOnlyError):
            collection.delete("simple.md")

    def test_rename_raises_readonly(self, collection: Collection) -> None:
        """rename() raises ReadOnlyError on a read-only collection."""
        with pytest.raises(ReadOnlyError):
            collection.rename("simple.md", "renamed.md")


# ---------------------------------------------------------------------------
# Write operations
# ---------------------------------------------------------------------------


@pytest.fixture
def writable(vault_path: Path) -> Collection:
    """Writable Collection backed by the clean vault fixture."""
    col = _make_collection(vault_path, read_only=False)
    col.build_index()
    return col


@pytest.fixture
def writable_with_embeddings(
    vault_path: Path,
    tmp_path: Path,
    mock_provider: MockEmbeddingProvider,
) -> Collection:
    """Writable Collection with mock embeddings enabled."""
    col = Collection(
        source_dir=vault_path,
        embeddings_path=tmp_path / "embeddings",
        embedding_provider=mock_provider,
        read_only=False,
    )
    col.build_index()
    col.build_embeddings()
    return col


class TestWrite:
    def test_write_creates_new_file(
        self, writable: Collection, vault_path: Path
    ) -> None:
        """write() creates a new file on disk and returns created=True."""
        result = writable.write("new_note.md", "# New Note\n\nNew content.\n")

        assert isinstance(result, WriteResult)
        assert result.path == "new_note.md"
        assert result.created is True
        assert (vault_path / "new_note.md").is_file()
        assert "New content" in (vault_path / "new_note.md").read_text()

    def test_write_creates_intermediate_directories(
        self, writable: Collection, vault_path: Path
    ) -> None:
        """write() creates intermediate dirs as needed."""
        writable.write("deep/nested/note.md", "# Deep\n\nNested.\n")

        assert (vault_path / "deep" / "nested" / "note.md").is_file()

    def test_write_overwrites_existing_file(
        self, writable: Collection, vault_path: Path
    ) -> None:
        """write() overwrites existing file and returns created=False."""
        result = writable.write("simple.md", "# Replaced\n\nNew body.\n")

        assert result.created is False
        assert "Replaced" in (vault_path / "simple.md").read_text()

    def test_write_with_frontmatter(
        self, writable: Collection, vault_path: Path
    ) -> None:
        """write() serialises frontmatter as YAML header."""
        writable.write(
            "with_fm.md",
            "# Hello\n\nBody text.\n",
            frontmatter={"title": "Hello", "tags": ["a", "b"]},
        )

        content = (vault_path / "with_fm.md").read_text()
        assert "---" in content
        assert "title: Hello" in content

    def test_write_immediately_searchable(self, writable: Collection) -> None:
        """Written content is immediately searchable."""
        writable.write(
            "searchable.md", "# Unique Xylophone\n\nRare content for testing.\n"
        )

        results = writable.search("xylophone", mode="keyword")
        paths = [r.path for r in results]
        assert "searchable.md" in paths

    def test_write_triggers_callback(self, vault_path: Path) -> None:
        """write() invokes the on_write callback with correct arguments."""
        calls: list = []
        col = _make_collection(
            vault_path, read_only=False, on_write=lambda *args: calls.append(args)
        )
        col.build_index()

        col.write("cb_test.md", "# Callback\n\nTest.\n")
        col.close()  # drain deferred callback queue

        assert len(calls) == 1
        path, content, operation = calls[0]
        assert path == vault_path / "cb_test.md"
        assert "Callback" in content
        assert operation == "write"

    def test_write_path_traversal_rejected(self, writable: Collection) -> None:
        """write() rejects paths that escape the source directory."""
        with pytest.raises(ValueError, match="traversal"):
            writable.write("../../etc/passwd.md", "malicious")

    def test_write_non_md_extension_rejected(self, writable: Collection) -> None:
        """write() rejects paths that do not end with .md."""
        with pytest.raises(ValueError, match=r"\.md"):
            writable.write("notes.yaml", "content")

    def test_write_updates_vector_index(
        self, writable_with_embeddings: Collection
    ) -> None:
        """write() with embeddings configured makes the doc findable via semantic search."""
        writable_with_embeddings.write(
            "new_semantic.md",
            "# Unique Quantum Entanglement\n\nContent about quantum physics.\n",
        )

        results = writable_with_embeddings.search(
            "quantum entanglement", mode="semantic"
        )
        paths = [r.path for r in results]
        assert "new_semantic.md" in paths

    def test_write_frontmatter_roundtrip(self, writable: Collection) -> None:
        """write() with frontmatter dict round-trips correctly through read()."""
        frontmatter = {
            "title": "Roundtrip Note",
            "tags": ["alpha", "beta"],
            "meta": {"key": "value"},
        }
        writable.write("roundtrip.md", "# Body\n\nContent.\n", frontmatter=frontmatter)

        result = writable.read("roundtrip.md")

        assert result is not None
        assert result.frontmatter["title"] == "Roundtrip Note"
        assert result.frontmatter["tags"] == ["alpha", "beta"]
        assert result.frontmatter["meta"] == {"key": "value"}

    def test_write_empty_content(self, writable: Collection) -> None:
        """write() with empty body and frontmatter produces a readable document."""
        writable.write("empty_body.md", "", frontmatter={"title": "Empty Body"})

        result = writable.read("empty_body.md")

        assert result is not None
        assert result.frontmatter["title"] == "Empty Body"

    def test_write_unicode_content(self, writable: Collection) -> None:
        """write() with Unicode and emoji content produces a searchable document."""
        writable.write(
            "unicode_note.md",
            "# Unicode Test\n\nCafé naïve résumé \U0001f600\n",
        )

        results = writable.search("unicode test", mode="keyword")
        paths = [r.path for r in results]
        assert "unicode_note.md" in paths


class TestEdit:
    def test_edit_replaces_text(self, writable: Collection, vault_path: Path) -> None:
        """edit() replaces exactly one occurrence of old_text."""
        result = writable.edit("simple.md", "Simple Document", "Updated Document")

        assert isinstance(result, EditResult)
        assert result.path == "simple.md"
        assert result.replacements == 1

        content = (vault_path / "simple.md").read_text()
        assert "Updated Document" in content
        assert "Simple Document" not in content

    def test_edit_empty_old_text_raises(self, writable: Collection) -> None:
        """edit() raises ValueError when old_text is empty."""
        with pytest.raises(ValueError, match="old_text must not be empty"):
            writable.edit("simple.md", "", "new")

    def test_edit_not_found_raises(self, writable: Collection) -> None:
        """edit() raises DocumentNotFoundError for missing files."""
        with pytest.raises(DocumentNotFoundError):
            writable.edit("nonexistent.md", "old", "new")

    def test_edit_old_text_missing_raises(self, writable: Collection) -> None:
        """edit() raises EditConflictError when old_text is not found."""
        with pytest.raises(EditConflictError, match="not found"):
            writable.edit("simple.md", "text that does not exist", "new")

    def test_edit_old_text_multiple_raises(
        self,
        writable: Collection,
    ) -> None:
        """edit() raises EditConflictError when old_text appears multiple times."""
        # Create a file with repeated content.
        writable.write("repeated.md", "word word word\n")

        with pytest.raises(EditConflictError, match="3 times"):
            writable.edit("repeated.md", "word", "replaced")

    def test_edit_updates_index(self, writable: Collection) -> None:
        """Edited content is immediately searchable."""
        writable.write("editable.md", "# Old Title\n\nOld body text.\n")
        writable.edit("editable.md", "Old Title", "New Unique Xylophone Title")

        results = writable.search("xylophone", mode="keyword")
        paths = [r.path for r in results]
        assert "editable.md" in paths

    def test_edit_triggers_callback(self, vault_path: Path) -> None:
        """edit() invokes the on_write callback with correct arguments."""
        calls: list = []
        col = _make_collection(
            vault_path, read_only=False, on_write=lambda *args: calls.append(args)
        )
        col.build_index()

        col.edit("simple.md", "Simple Document", "Modified Document")
        col.close()  # drain deferred callback queue

        assert len(calls) == 1
        _, _, operation = calls[0]
        assert operation == "edit"

    def test_edit_old_content_removed_from_fts(self, writable: Collection) -> None:
        """edit() removes the old content from FTS; old text is no longer searchable."""
        writable.write("editable_fts.md", "# OldUniqueTitle\n\nOld body text.\n")

        # Confirm old text is searchable before edit.
        before = writable.search("OldUniqueTitle", mode="keyword")
        assert any(r.path == "editable_fts.md" for r in before)

        writable.edit("editable_fts.md", "OldUniqueTitle", "NewReplacedTitle")

        # Old text must no longer appear in results.
        after_old = writable.search("OldUniqueTitle", mode="keyword")
        assert not any(r.path == "editable_fts.md" for r in after_old)

    def test_edit_updates_vector_index(
        self, writable_with_embeddings: Collection
    ) -> None:
        """edit() with embeddings configured reflects new content in semantic search."""
        writable_with_embeddings.write(
            "vec_editable.md",
            "# Original Content\n\nThis is the original text.\n",
        )
        writable_with_embeddings.edit(
            "vec_editable.md",
            "original text",
            "quantum mechanics discussion",
        )

        results = writable_with_embeddings.search("quantum mechanics", mode="semantic")
        paths = [r.path for r in results]
        assert "vec_editable.md" in paths


class TestDelete:
    def test_delete_removes_file(self, writable: Collection, vault_path: Path) -> None:
        """delete() removes the file from disk."""
        result = writable.delete("simple.md")

        assert isinstance(result, DeleteResult)
        assert result.path == "simple.md"
        assert not (vault_path / "simple.md").is_file()

    def test_delete_not_found_raises(self, writable: Collection) -> None:
        """delete() raises DocumentNotFoundError for missing files."""
        with pytest.raises(DocumentNotFoundError):
            writable.delete("nonexistent.md")

    def test_delete_removes_from_search(self, writable: Collection) -> None:
        """Deleted content no longer appears in search results."""
        # Verify it's searchable first.
        results_before = writable.search("Simple Document", mode="keyword")
        assert any(r.path == "simple.md" for r in results_before)

        writable.delete("simple.md")

        results_after = writable.search("Simple Document", mode="keyword")
        assert not any(r.path == "simple.md" for r in results_after)

    def test_delete_triggers_callback(self, vault_path: Path) -> None:
        """delete() invokes the on_write callback with empty content."""
        calls: list = []
        col = _make_collection(
            vault_path, read_only=False, on_write=lambda *args: calls.append(args)
        )
        col.build_index()

        col.delete("simple.md")
        col.close()  # drain deferred callback queue

        assert len(calls) == 1
        path, content, operation = calls[0]
        assert path == vault_path / "simple.md"
        assert content == ""
        assert operation == "delete"

    def test_delete_removes_from_vector_index(
        self, writable_with_embeddings: Collection
    ) -> None:
        """delete() removes the document from semantic search results."""
        # Confirm the doc is reachable via semantic search first.
        before = writable_with_embeddings.search("simple document", mode="semantic")
        assert any(r.path == "simple.md" for r in before)

        writable_with_embeddings.delete("simple.md")

        after = writable_with_embeddings.search("simple document", mode="semantic")
        assert not any(r.path == "simple.md" for r in after)


class TestRename:
    def test_rename_moves_file(self, writable: Collection, vault_path: Path) -> None:
        """rename() moves the file on disk."""
        result = writable.rename("simple.md", "moved.md")

        assert isinstance(result, RenameResult)
        assert result.old_path == "simple.md"
        assert result.new_path == "moved.md"
        assert not (vault_path / "simple.md").is_file()
        assert (vault_path / "moved.md").is_file()

    def test_rename_updates_search(self, writable: Collection) -> None:
        """After rename, search finds the document at the new path only."""
        writable.rename("simple.md", "moved.md")

        results = writable.search("Simple Document", mode="keyword")
        paths = [r.path for r in results]
        assert "moved.md" in paths
        assert "simple.md" not in paths

    def test_rename_creates_intermediate_dirs(
        self, writable: Collection, vault_path: Path
    ) -> None:
        """rename() creates intermediate directories for the new path."""
        writable.rename("simple.md", "new_folder/moved.md")

        assert (vault_path / "new_folder" / "moved.md").is_file()

    def test_rename_not_found_raises(self, writable: Collection) -> None:
        """rename() raises DocumentNotFoundError when old_path missing."""
        with pytest.raises(DocumentNotFoundError):
            writable.rename("nonexistent.md", "target.md")

    def test_rename_target_exists_raises(self, writable: Collection) -> None:
        """rename() raises DocumentExistsError when new_path exists."""
        with pytest.raises(DocumentExistsError):
            writable.rename("simple.md", "no_frontmatter.md")

    def test_rename_triggers_callback(self, vault_path: Path) -> None:
        """rename() invokes the on_write callback with new path."""
        calls: list = []
        col = _make_collection(
            vault_path, read_only=False, on_write=lambda *args: calls.append(args)
        )
        col.build_index()

        col.rename("simple.md", "moved.md")
        col.close()  # drain deferred callback queue

        assert len(calls) == 1
        path, content, operation = calls[0]
        assert path == vault_path / "moved.md"
        assert content != ""
        assert operation == "rename"

    def test_rename_folder_updated(self, writable: Collection) -> None:
        """rename() updates the folder derivation after move."""
        writable.rename("simple.md", "new_folder/simple.md")

        notes = writable.list(folder="new_folder")
        paths = [n.path for n in notes]
        assert "new_folder/simple.md" in paths

    def test_rename_preserves_file_content(
        self, writable: Collection, vault_path: Path
    ) -> None:
        """rename() produces a file whose content is byte-identical to the original."""
        original_bytes = (vault_path / "simple.md").read_bytes()

        writable.rename("simple.md", "preserved.md")

        renamed_bytes = (vault_path / "preserved.md").read_bytes()
        assert renamed_bytes == original_bytes

    def test_rename_old_path_removed_from_fts(self, writable: Collection) -> None:
        """rename() removes the old path from FTS; old path is no longer searchable."""
        # Confirm old path is searchable before rename.
        before = writable.search("Simple Document", mode="keyword")
        assert any(r.path == "simple.md" for r in before)

        writable.rename("simple.md", "after_rename.md")

        after = writable.search("Simple Document", mode="keyword")
        assert not any(r.path == "simple.md" for r in after)

    def test_rename_updates_vector_index(
        self, writable_with_embeddings: Collection
    ) -> None:
        """rename() with embeddings configured indexes the new path, drops the old."""
        writable_with_embeddings.rename("simple.md", "renamed_semantic.md")

        after = writable_with_embeddings.search("simple document", mode="semantic")
        paths = [r.path for r in after]
        assert "renamed_semantic.md" in paths
        assert "simple.md" not in paths

    def test_rename_to_same_path_raises(self, writable: Collection) -> None:
        """rename() to the same path raises DocumentExistsError."""
        with pytest.raises(DocumentExistsError):
            writable.rename("simple.md", "simple.md")


# ---------------------------------------------------------------------------
# Concurrent write safety
# ---------------------------------------------------------------------------


class TestConcurrentWrites:
    def test_concurrent_writes(self, writable: Collection, vault_path: Path) -> None:
        """10 simultaneous write() calls to distinct paths all succeed.

        Uses :class:`concurrent.futures.ThreadPoolExecutor` to exercise the
        ``_write_lock`` on a single Collection instance.
        """
        paths = [f"concurrent_write_{i}.md" for i in range(10)]

        def do_write(p: str) -> None:
            writable.write(p, f"# Note {p}\n\nContent for {p}.\n")

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(do_write, p) for p in paths]
            for fut in concurrent.futures.as_completed(futures):
                fut.result()  # re-raise any exception from the thread

        # All 10 files must exist on disk.
        for p in paths:
            assert (vault_path / p).is_file(), f"Expected {p} to exist on disk"

        # All 10 files must be discoverable via search.
        results = writable.search("Content for", mode="keyword", limit=20)
        result_paths = {r.path for r in results}
        for p in paths:
            assert p in result_paths, f"Expected {p} to be searchable"

    def test_concurrent_write_and_edit(
        self, writable: Collection, vault_path: Path
    ) -> None:
        """Simultaneous edit() calls on distinct unique strings do not corrupt data.

        Writes a file with 10 uniquely-labelled sections, then submits 10
        concurrent edits each replacing a different label.  Verifies all
        replacements land without data loss.
        """
        # Build a file where each line contains a unique token.
        lines = [f"Section-Token-{i}: original text\n" for i in range(10)]
        writable.write("concurrent_edit.md", "".join(lines))

        def do_edit(i: int) -> None:
            writable.edit(
                "concurrent_edit.md",
                f"Section-Token-{i}: original text",
                f"Section-Token-{i}: replaced text",
            )

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(do_edit, i) for i in range(10)]
            for fut in concurrent.futures.as_completed(futures):
                fut.result()

        # All replacements must appear in the final file; none lost.
        final = (vault_path / "concurrent_edit.md").read_text(encoding="utf-8")
        for i in range(10):
            assert f"Section-Token-{i}: replaced text" in final, (
                f"Token {i} replacement missing from final file"
            )
            assert f"Section-Token-{i}: original text" not in final, (
                f"Token {i} original text still present after replacement"
            )

    def test_pause_writes_blocks_write(self, vault_path: Path) -> None:
        """pause_writes() queues write() calls until the context exits."""
        import threading
        import time

        col = Collection(source_dir=vault_path, read_only=False)

        finished = threading.Event()

        def do_write() -> None:
            col.write("paused.md", "# Paused\n")
            finished.set()

        with col.pause_writes():
            t = threading.Thread(target=do_write, daemon=True)
            t.start()
            time.sleep(0.05)
            assert not finished.is_set()

        t.join(timeout=2.0)
        assert finished.is_set()

    def test_start_and_close_delegate_to_git_strategy(self, vault_path: Path) -> None:
        """start() and close() call into the configured git strategy."""

        class DummyGitStrategy:
            def __init__(self) -> None:
                self.started: dict[str, object] | None = None
                self.stopped = False
                self.closed = False

            def start(
                self,
                *,
                repo_path: Path,
                pull_interval_s: int,
                pause_writes: object,
                on_pull: object,
            ) -> None:
                self.started = {
                    "repo_path": repo_path,
                    "pull_interval_s": pull_interval_s,
                    "pause_writes": pause_writes,
                    "on_pull": on_pull,
                }

            def stop(self) -> None:
                self.stopped = True

            def close(self) -> None:
                self.closed = True

            def sync_once(self, _repo_path: Path) -> bool:
                return False

        git_strategy = DummyGitStrategy()
        col = Collection(
            source_dir=vault_path,
            read_only=False,
            git_strategy=git_strategy,  # type: ignore[arg-type]
            git_pull_interval_s=60,
        )

        col.start()
        assert git_strategy.started is not None
        assert git_strategy.started["repo_path"] == vault_path
        assert git_strategy.started["pull_interval_s"] == 60

        # Verify Collection.stop() delegates to git_strategy.stop() (Option C lifecycle).
        col.stop()
        assert git_strategy.stopped is True

        col.close()
        assert git_strategy.closed is True

    def test_sync_from_remote_before_index_calls_sync_once(
        self, vault_path: Path
    ) -> None:
        """sync_from_remote_before_index() calls git sync when enabled."""

        class DummyGitStrategy:
            def __init__(self) -> None:
                self.calls: list[Path] = []

            def sync_once(self, repo_path: Path) -> bool:
                self.calls.append(repo_path)
                return False

            def close(self) -> None:
                return None

        git_strategy = DummyGitStrategy()
        col = Collection(
            source_dir=vault_path,
            git_strategy=git_strategy,  # type: ignore[arg-type]
            git_pull_interval_s=60,
        )

        col.sync_from_remote_before_index()
        assert git_strategy.calls == [vault_path]


# ---------------------------------------------------------------------------
# Attachment helpers
# ---------------------------------------------------------------------------


class TestAttachmentHelpers:
    def test_is_attachment_pdf(self, vault_path: Path) -> None:
        """_is_attachment() returns True for a .pdf path with default allowlist."""
        col = Collection(source_dir=vault_path)
        assert col._is_attachment("assets/report.pdf") is True

    def test_is_attachment_md_always_false(self, vault_path: Path) -> None:
        """_is_attachment() always returns False for .md paths."""
        col = Collection(source_dir=vault_path)
        assert col._is_attachment("notes/note.md") is False

    def test_is_attachment_disallowed_extension(self, vault_path: Path) -> None:
        """_is_attachment() returns False for extensions not in the default list."""
        col = Collection(source_dir=vault_path)
        # .xyz is not in the default list
        assert col._is_attachment("file.xyz") is False

    def test_is_attachment_wildcard_allows_all(self, vault_path: Path) -> None:
        """_is_attachment() returns True for any non-.md extension when '*' is set."""
        col = Collection(source_dir=vault_path, attachment_extensions=["*"])
        assert col._is_attachment("file.xyz") is True
        assert col._is_attachment("file.bin") is True
        assert col._is_attachment("notes/note.md") is False

    def test_validate_attachment_path_rejects_md(self, vault_path: Path) -> None:
        """_validate_attachment_path() raises ValueError for .md paths."""
        col = Collection(source_dir=vault_path)
        with pytest.raises(ValueError, match=r"\.md"):
            col._validate_attachment_path("note.md")

    def test_validate_attachment_path_rejects_traversal(self, vault_path: Path) -> None:
        """_validate_attachment_path() raises ValueError on path traversal."""
        col = Collection(source_dir=vault_path)
        with pytest.raises(ValueError, match="traversal"):
            col._validate_attachment_path("../../etc/passwd.pdf")

    def test_validate_attachment_path_rejects_disallowed_ext(
        self, vault_path: Path
    ) -> None:
        """_validate_attachment_path() raises ValueError for disallowed extensions."""
        col = Collection(source_dir=vault_path)
        with pytest.raises(ValueError, match="allowlist"):
            col._validate_attachment_path("file.xyz")


# ---------------------------------------------------------------------------
# read_attachment / write_attachment
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_with_attachment(vault_path: Path) -> Path:
    """Vault fixture with a sample PDF-like binary file."""
    (vault_path / "assets").mkdir()
    (vault_path / "assets" / "report.pdf").write_bytes(b"%PDF-1.4 fake content")
    (vault_path / "assets" / "image.png").write_bytes(
        b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
    )
    return vault_path


class TestReadAttachment:
    def test_read_attachment_returns_content(self, vault_with_attachment: Path) -> None:
        """read_attachment() returns base64-encoded content and mime type."""
        import base64

        col = Collection(source_dir=vault_with_attachment)
        result = col.read_attachment("assets/report.pdf")

        assert isinstance(result, AttachmentContent)
        assert result.path == "assets/report.pdf"
        assert result.mime_type == "application/pdf"
        assert result.size_bytes == len(b"%PDF-1.4 fake content")
        decoded = base64.b64decode(result.content_base64)
        assert decoded == b"%PDF-1.4 fake content"

    def test_read_attachment_not_found_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """read_attachment() raises ValueError for missing files."""
        col = Collection(source_dir=vault_with_attachment)
        with pytest.raises(ValueError, match="not found"):
            col.read_attachment("assets/missing.pdf")

    def test_read_attachment_disallowed_extension_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """read_attachment() raises ValueError for disallowed extensions."""
        col = Collection(source_dir=vault_with_attachment)
        with pytest.raises(ValueError, match="allowlist"):
            col.read_attachment("assets/report.xyz")

    def test_read_attachment_size_limit_enforced(
        self, vault_with_attachment: Path
    ) -> None:
        """read_attachment() raises ValueError when file exceeds the size limit."""
        # 1-byte limit
        col = Collection(
            source_dir=vault_with_attachment, max_attachment_size_mb=0.000001
        )
        with pytest.raises(ValueError, match="exceeds"):
            col.read_attachment("assets/report.pdf")

    def test_read_attachment_zero_size_limit_disables(
        self, vault_with_attachment: Path
    ) -> None:
        """read_attachment() with max_attachment_size_mb=0 has no size limit."""
        col = Collection(source_dir=vault_with_attachment, max_attachment_size_mb=0)
        result = col.read_attachment("assets/report.pdf")
        assert result.size_bytes > 0

    def test_read_attachment_png_mime_type(self, vault_with_attachment: Path) -> None:
        """read_attachment() detects image/png MIME type."""
        col = Collection(source_dir=vault_with_attachment)
        result = col.read_attachment("assets/image.png")
        assert result.mime_type == "image/png"

    def test_read_attachment_returns_etag(self, vault_with_attachment: Path) -> None:
        """read_attachment() returns an etag field containing the SHA256 hex digest."""
        from markdown_vault_mcp.hashing import compute_file_hash

        col = Collection(source_dir=vault_with_attachment)
        result = col.read_attachment("assets/report.pdf")

        expected = compute_file_hash(vault_with_attachment / "assets" / "report.pdf")
        assert result.etag == expected

    def test_read_attachment_etag_is_stable(self, vault_with_attachment: Path) -> None:
        """read_attachment() returns the same etag on repeated reads."""
        col = Collection(source_dir=vault_with_attachment)
        result1 = col.read_attachment("assets/report.pdf")
        result2 = col.read_attachment("assets/report.pdf")

        assert result1.etag is not None
        assert result1.etag == result2.etag
        assert len(result1.etag) == 64  # SHA256 hex is 64 chars

    def test_read_attachment_etag_changes_after_write(
        self, vault_with_attachment: Path
    ) -> None:
        """read_attachment() etag changes when file content changes."""
        col = Collection(source_dir=vault_with_attachment, read_only=False)
        result_before = col.read_attachment("assets/report.pdf")
        etag_before = result_before.etag

        col.write_attachment("assets/report.pdf", b"new content")

        result_after = col.read_attachment("assets/report.pdf")
        assert result_after.etag != etag_before


class TestWriteAttachment:
    def test_write_attachment_creates_file(self, vault_with_attachment: Path) -> None:
        """write_attachment() creates a new binary file on disk."""
        col = Collection(source_dir=vault_with_attachment, read_only=False)
        raw = b"\x89PNG\r\n\x1a\n" + b"\x00" * 8
        result = col.write_attachment("assets/new.png", raw)

        assert isinstance(result, WriteResult)
        assert result.path == "assets/new.png"
        assert result.created is True
        assert (vault_with_attachment / "assets" / "new.png").read_bytes() == raw

    def test_write_attachment_overwrites_existing(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() overwrites an existing file, returns created=False."""
        col = Collection(source_dir=vault_with_attachment, read_only=False)
        new_content = b"new pdf content"
        result = col.write_attachment("assets/report.pdf", new_content)

        assert result.created is False
        assert (
            vault_with_attachment / "assets" / "report.pdf"
        ).read_bytes() == new_content

    def test_write_attachment_creates_intermediate_dirs(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() creates parent directories as needed."""
        col = Collection(source_dir=vault_with_attachment, read_only=False)
        col.write_attachment("deep/nested/file.pdf", b"content")

        assert (vault_with_attachment / "deep" / "nested" / "file.pdf").is_file()

    def test_write_attachment_readonly_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() raises ReadOnlyError on a read-only collection."""
        col = Collection(source_dir=vault_with_attachment, read_only=True)
        with pytest.raises(ReadOnlyError):
            col.write_attachment("assets/new.pdf", b"content")

    def test_write_attachment_size_limit_enforced(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() raises ValueError when content exceeds size limit."""
        col = Collection(
            source_dir=vault_with_attachment,
            read_only=False,
            max_attachment_size_mb=0.000001,
        )
        with pytest.raises(ValueError, match="exceeds"):
            col.write_attachment("assets/big.pdf", b"a" * 100)

    def test_write_attachment_disallowed_extension_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() raises ValueError for disallowed extensions."""
        col = Collection(source_dir=vault_with_attachment, read_only=False)
        with pytest.raises(ValueError, match="allowlist"):
            col.write_attachment("file.xyz", b"content")

    def test_write_attachment_triggers_callback(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() invokes the on_write callback."""
        calls: list = []
        col = Collection(
            source_dir=vault_with_attachment,
            read_only=False,
            on_write=lambda *args: calls.append(args),
        )
        col.write_attachment("assets/cb.pdf", b"callback test")
        col.close()  # drain deferred callback queue

        assert len(calls) == 1
        path, content, operation = calls[0]
        assert path == vault_with_attachment / "assets" / "cb.pdf"
        assert content == ""  # binary — empty string passed to callback
        assert operation == "write"


# ---------------------------------------------------------------------------
# list() with include_attachments
# ---------------------------------------------------------------------------


class TestListWithAttachments:
    def test_list_default_excludes_attachments(
        self, vault_with_attachment: Path
    ) -> None:
        """list() without include_attachments does not return attachment files."""
        col = Collection(source_dir=vault_with_attachment)
        col.build_index()
        results = col.list()

        paths = [r.path for r in results]
        assert not any(p.endswith(".pdf") or p.endswith(".png") for p in paths)

    def test_list_include_attachments_returns_both(
        self, vault_with_attachment: Path
    ) -> None:
        """list(include_attachments=True) returns notes and attachments."""
        col = Collection(source_dir=vault_with_attachment)
        col.build_index()
        results = col.list(include_attachments=True)

        kinds = {type(r).__name__ for r in results}
        assert "NoteInfo" in kinds
        assert "AttachmentInfo" in kinds

    def test_list_attachment_info_fields(self, vault_with_attachment: Path) -> None:
        """AttachmentInfo entries have the correct fields."""
        col = Collection(source_dir=vault_with_attachment)
        col.build_index()
        results = col.list(include_attachments=True)

        attachments = [r for r in results if isinstance(r, AttachmentInfo)]
        assert len(attachments) >= 1

        pdf = next(a for a in attachments if a.path.endswith(".pdf"))
        assert pdf.kind == "attachment"
        assert pdf.mime_type == "application/pdf"
        assert pdf.size_bytes > 0
        assert pdf.folder == "assets"

    def test_list_attachments_excluded_when_not_in_allowlist(
        self, vault_with_attachment: Path
    ) -> None:
        """Attachments with disallowed extensions are not returned."""
        (vault_with_attachment / "assets" / "data.xyz").write_bytes(b"unknown")
        col = Collection(source_dir=vault_with_attachment)
        col.build_index()
        results = col.list(include_attachments=True)

        paths = [r.path for r in results]
        assert not any(p.endswith(".xyz") for p in paths)

    def test_list_attachments_wildcard_includes_all(
        self, vault_with_attachment: Path
    ) -> None:
        """attachment_extensions=['*'] returns all non-.md files."""
        (vault_with_attachment / "assets" / "data.xyz").write_bytes(b"unknown")
        col = Collection(source_dir=vault_with_attachment, attachment_extensions=["*"])
        col.build_index()
        results = col.list(include_attachments=True)

        paths = [r.path for r in results]
        assert any(p.endswith(".xyz") for p in paths)

    def test_list_attachments_folder_filter(self, vault_with_attachment: Path) -> None:
        """list(include_attachments=True, folder=...) filters attachments by folder."""
        col = Collection(source_dir=vault_with_attachment)
        col.build_index()
        results = col.list(include_attachments=True, folder="assets")

        for r in results:
            assert r.folder == "assets" or r.folder.startswith("assets/")


# ---------------------------------------------------------------------------
# delete() and rename() for attachments
# ---------------------------------------------------------------------------


class TestDeleteAttachment:
    def test_delete_attachment_removes_file(self, vault_with_attachment: Path) -> None:
        """delete() removes an attachment file from disk."""
        col = Collection(source_dir=vault_with_attachment, read_only=False)
        col.build_index()
        result = col.delete("assets/report.pdf")

        assert isinstance(result, DeleteResult)
        assert result.path == "assets/report.pdf"
        assert not (vault_with_attachment / "assets" / "report.pdf").is_file()

    def test_delete_attachment_not_found_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """delete() raises DocumentNotFoundError for missing attachment."""
        col = Collection(source_dir=vault_with_attachment, read_only=False)
        col.build_index()
        with pytest.raises(DocumentNotFoundError):
            col.delete("assets/missing.pdf")

    def test_delete_attachment_disallowed_ext_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """delete() on a disallowed extension raises ValueError."""
        (vault_with_attachment / "file.xyz").write_bytes(b"data")
        col = Collection(source_dir=vault_with_attachment, read_only=False)
        col.build_index()
        with pytest.raises(ValueError, match="allowlist"):
            col.delete("file.xyz")

    def test_delete_attachment_triggers_callback(
        self, vault_with_attachment: Path
    ) -> None:
        """delete() on an attachment invokes the on_write callback."""
        calls: list = []
        col = Collection(
            source_dir=vault_with_attachment,
            read_only=False,
            on_write=lambda *args: calls.append(args),
        )
        col.build_index()
        col.delete("assets/report.pdf")
        col.close()  # drain deferred callback queue

        assert len(calls) == 1
        _, _, operation = calls[0]
        assert operation == "delete"


class TestRenameAttachment:
    def test_rename_attachment_moves_file(self, vault_with_attachment: Path) -> None:
        """rename() moves an attachment file on disk."""
        col = Collection(source_dir=vault_with_attachment, read_only=False)
        col.build_index()
        result = col.rename("assets/report.pdf", "docs/report.pdf")

        assert isinstance(result, RenameResult)
        assert not (vault_with_attachment / "assets" / "report.pdf").is_file()
        assert (vault_with_attachment / "docs" / "report.pdf").is_file()

    def test_rename_attachment_not_found_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """rename() raises DocumentNotFoundError for missing attachment."""
        col = Collection(source_dir=vault_with_attachment, read_only=False)
        col.build_index()
        with pytest.raises(DocumentNotFoundError):
            col.rename("assets/missing.pdf", "docs/report.pdf")

    def test_rename_attachment_target_exists_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """rename() raises DocumentExistsError when the target already exists."""
        col = Collection(source_dir=vault_with_attachment, read_only=False)
        col.build_index()
        with pytest.raises(DocumentExistsError):
            col.rename("assets/report.pdf", "assets/image.png")

    def test_rename_attachment_creates_intermediate_dirs(
        self, vault_with_attachment: Path
    ) -> None:
        """rename() creates parent directories for the attachment target."""
        col = Collection(source_dir=vault_with_attachment, read_only=False)
        col.build_index()
        col.rename("assets/report.pdf", "new_folder/sub/report.pdf")

        assert (vault_with_attachment / "new_folder" / "sub" / "report.pdf").is_file()

    def test_rename_attachment_preserves_content(
        self, vault_with_attachment: Path
    ) -> None:
        """rename() produces a file byte-identical to the original."""
        original = (vault_with_attachment / "assets" / "report.pdf").read_bytes()
        col = Collection(source_dir=vault_with_attachment, read_only=False)
        col.build_index()
        col.rename("assets/report.pdf", "docs/report.pdf")

        assert (vault_with_attachment / "docs" / "report.pdf").read_bytes() == original


# ---------------------------------------------------------------------------
# stats() includes attachment_extensions
# ---------------------------------------------------------------------------


class TestStatsAttachmentExtensions:
    def test_stats_includes_attachment_extensions_default(
        self, collection: Collection
    ) -> None:
        """stats() includes attachment_extensions from the default allowlist."""
        s = collection.stats()
        assert isinstance(s.attachment_extensions, list)
        assert "pdf" in s.attachment_extensions
        assert "png" in s.attachment_extensions

    def test_stats_includes_attachment_extensions_custom(
        self, vault_path: Path
    ) -> None:
        """stats() reflects a custom attachment_extensions list."""
        col = Collection(source_dir=vault_path, attachment_extensions=["pdf", "docx"])
        col.build_index()
        s = col.stats()
        assert sorted(s.attachment_extensions) == ["docx", "pdf"]

    def test_stats_includes_attachment_extensions_wildcard(
        self, vault_path: Path
    ) -> None:
        """stats() shows ['*'] when attachment_extensions is the wildcard."""
        col = Collection(source_dir=vault_path, attachment_extensions=["*"])
        col.build_index()
        s = col.stats()
        assert s.attachment_extensions == ["*"]


# ---------------------------------------------------------------------------
# Semantic search
# ---------------------------------------------------------------------------


@pytest.fixture
def semantic_collection(
    vault_path: Path,
    tmp_path: Path,
    mock_provider: MockEmbeddingProvider,
) -> Collection:
    """Collection with FTS index and vector embeddings fully built."""
    col = Collection(
        source_dir=vault_path,
        embeddings_path=tmp_path / "embeddings",
        embedding_provider=mock_provider,
    )
    col.build_index()
    col.build_embeddings()
    return col


class TestSemanticSearch:
    def test_semantic_search_returns_semantic_type(
        self, semantic_collection: Collection
    ) -> None:
        """search(mode='semantic') returns results with search_type='semantic'."""
        results = semantic_collection.search("document content", mode="semantic")

        assert len(results) > 0
        assert all(r.search_type == "semantic" for r in results)

    def test_semantic_search_result_fields(
        self, semantic_collection: Collection
    ) -> None:
        """Semantic search results carry path, title, score, and frontmatter fields."""
        results = semantic_collection.search("document content", mode="semantic")

        assert len(results) > 0
        for r in results:
            assert r.path
            # Cosine similarity ranges from -1 to 1; just verify it is a float.
            assert isinstance(r.score, float)
            assert isinstance(r.frontmatter, dict)

    def test_semantic_search_limit_respected(
        self, semantic_collection: Collection
    ) -> None:
        """search(mode='semantic', limit=N) never returns more than N results."""
        results = semantic_collection.search("content", mode="semantic", limit=2)

        assert len(results) <= 2

    def test_semantic_search_folder_filter(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """search(mode='semantic', folder='subfolder') returns only subfolder docs."""
        col = Collection(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
        )
        col.build_index()
        col.build_embeddings()

        results = col.search("document content", mode="semantic", folder="subfolder")

        # All results must be in the requested folder or a sub-folder of it.
        assert len(results) > 0, "Expected at least one result in subfolder"
        for r in results:
            assert r.folder == "subfolder" or r.folder.startswith("subfolder/")

    def test_semantic_search_tag_filter(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """search(mode='semantic', filters={...}) filters by frontmatter value."""
        col = Collection(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
            indexed_frontmatter_fields=["cluster"],
        )
        col.build_index()
        col.build_embeddings()

        # full_frontmatter.md has cluster=fiction; all results must match.
        results = col.search(
            "document", mode="semantic", filters={"cluster": "fiction"}, limit=10
        )

        assert len(results) > 0, "Expected at least one result with cluster=fiction"
        for r in results:
            assert r.frontmatter.get("cluster") == "fiction"

    def test_semantic_search_no_provider_raises(self, vault_path: Path) -> None:
        """search(mode='semantic') raises ValueError when provider is not configured."""
        col = _make_collection(vault_path)
        col.build_index()

        with pytest.raises(ValueError, match="embedding_provider"):
            col.search("query", mode="semantic")

    def test_load_vectors_creates_empty_when_no_npy(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """_load_vectors() creates an empty VectorIndex when no .npy file exists."""
        embeddings_path = tmp_path / "embeddings"
        col = Collection(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.build_index()

        # Confirm no .npy file exists before loading.
        assert not (tmp_path / "embeddings.npy").exists()

        vectors = col._load_vectors()

        assert vectors is not None
        assert vectors.count == 0

    def test_load_vectors_loads_from_disk(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """_load_vectors() loads persisted embeddings when .npy file exists."""
        embeddings_path = tmp_path / "embeddings"

        # Build and persist the vector index.
        col1 = Collection(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col1.build_index()
        chunk_count = col1.build_embeddings()
        assert chunk_count > 0

        # Create a fresh collection pointing at the same paths — vectors not yet loaded.
        col2 = Collection(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col2.build_index()
        assert col2._vectors is None  # not yet loaded

        vectors = col2._load_vectors()

        assert vectors.count == chunk_count

    def test_load_vectors_provider_mismatch_rebuilds(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """_load_vectors() rebuilds when persisted provider/model metadata differs."""
        embeddings_path = tmp_path / "embeddings"

        col1 = Collection(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col1.build_index()
        expected_count = col1.build_embeddings()
        assert expected_count > 0

        class AlternateProvider(type(mock_provider)):
            @property
            def provider_name(self) -> str:
                return "alternate-mock"

            @property
            def model_name(self) -> str:
                return "alternate-model-v1"

        col2 = Collection(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=AlternateProvider(),
        )
        col2.build_index()

        vectors = col2._load_vectors()
        assert vectors.count == expected_count

        with (tmp_path / "embeddings.json").open(encoding="utf-8") as fh:
            payload = json.load(fh)
        assert payload["index_metadata"]["provider"] == "alternate-mock"
        assert payload["index_metadata"]["model"] == "alternate-model-v1"

    def test_require_vectors_raises_when_unconfigured(self, vault_path: Path) -> None:
        """_require_vectors() raises ValueError when provider/path are absent."""
        col = _make_collection(vault_path)

        with pytest.raises(ValueError, match="embedding_provider"):
            col._require_vectors()


# ---------------------------------------------------------------------------
# Hybrid search
# ---------------------------------------------------------------------------


class TestHybridSearch:
    def test_hybrid_search_returns_results(
        self, semantic_collection: Collection
    ) -> None:
        """search(mode='hybrid') returns at least one result."""
        results = semantic_collection.search("document content", mode="hybrid")

        assert len(results) > 0

    def test_hybrid_search_search_type_varies(
        self, semantic_collection: Collection
    ) -> None:
        """Hybrid results can carry 'keyword' or 'semantic' search_type."""
        results = semantic_collection.search("document content", mode="hybrid", limit=9)

        types = {r.search_type for r in results}
        # With 9 docs and a broad query, at least one type should appear.
        assert types.issubset({"keyword", "semantic"})
        assert len(types) >= 1

    def test_hybrid_search_folder_filter(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """search(mode='hybrid', folder='subfolder') confines results to that folder."""
        col = Collection(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
        )
        col.build_index()
        col.build_embeddings()

        results = col.search("nested document", mode="hybrid", folder="subfolder")

        assert len(results) > 0, "Expected at least one result in subfolder"
        for r in results:
            assert r.folder == "subfolder" or r.folder.startswith("subfolder/")

    def test_hybrid_search_tag_filter(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """search(mode='hybrid', filters={...}) filters semantic candidates by tag."""
        col = Collection(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
            indexed_frontmatter_fields=["cluster"],
        )
        col.build_index()
        col.build_embeddings()

        results = col.search(
            "document", mode="hybrid", filters={"cluster": "fiction"}, limit=10
        )

        # All results (keyword and semantic) must have the correct tag.
        # MockEmbeddingProvider uses hash-based vectors so semantic results
        # may not include the cluster=fiction doc; keyword results are reliable.
        assert len(results) > 0, "Expected at least one result with cluster=fiction"
        for r in results:
            assert r.frontmatter.get("cluster") == "fiction"

    def test_hybrid_rrf_boost_for_dual_match(
        self,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """A doc appearing in both keyword and semantic results has a higher RRF score.

        Strategy: build a vault with two documents.  Make one uniquely relevant
        to keyword search (exact term match).  Both appear in semantic search.
        The keyword-only match and the dual-match document's RRF scores are compared.
        """
        vault = tmp_path / "rrf_vault"
        vault.mkdir()
        # doc_a appears in both FTS (exact match) and semantic results.
        (vault / "doc_a.md").write_text(
            "# Zymurgy Note\n\nZymurgy is the study of fermentation.\n"
        )
        # doc_b appears only in semantic (no keyword match for 'zymurgy').
        (vault / "doc_b.md").write_text(
            "# Brewing Science\n\nBeer and fermentation science overview.\n"
        )
        embeddings_path = tmp_path / "rrf_emb"
        col = Collection(
            source_dir=vault,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.build_index()
        col.build_embeddings()

        results = col.search("zymurgy", mode="hybrid", limit=10)

        by_path = {r.path: r for r in results}
        # doc_a matched FTS (rank ~1) and semantic — should appear with a score.
        assert "doc_a.md" in by_path
        doc_a_score = by_path["doc_a.md"].score
        # If doc_b also appears, doc_a (dual match) must outscore doc_b (single match).
        if "doc_b.md" in by_path:
            assert doc_a_score >= by_path["doc_b.md"].score

    def test_hybrid_search_no_embeddings_raises(self, vault_path: Path) -> None:
        """search(mode='hybrid') raises ValueError without a configured provider."""
        col = _make_collection(vault_path)
        col.build_index()

        with pytest.raises(ValueError, match="embedding_provider"):
            col.search("query", mode="hybrid")


# ---------------------------------------------------------------------------
# embeddings_status()
# ---------------------------------------------------------------------------


class TestEmbeddingsStatus:
    def test_status_unavailable_when_no_provider(self, vault_path: Path) -> None:
        """embeddings_status() returns available=False when no provider is set."""
        col = _make_collection(vault_path)
        col.build_index()

        status = col.embeddings_status()

        assert status["available"] is False
        assert status["provider"] is None
        assert status["chunk_count"] == 0
        assert status["path"] is None

    def test_status_available_with_provider_before_build(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """embeddings_status() returns available=True with chunk_count=0 before build."""
        col = Collection(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
        )
        col.build_index()

        status = col.embeddings_status()

        assert status["available"] is True
        assert status["chunk_count"] == 0
        assert "MockEmbeddingProvider" in status["provider"]

    def test_status_chunk_count_after_build(
        self, semantic_collection: Collection
    ) -> None:
        """embeddings_status() returns correct chunk_count after build_embeddings()."""
        status = semantic_collection.embeddings_status()

        assert status["available"] is True
        # 9 documents, each one chunk — chunk_count must match.
        assert status["chunk_count"] == 9

    def test_status_reads_json_when_vectors_not_loaded(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """embeddings_status() reads chunk_count from JSON metadata without loading matrix.

        Builds embeddings with col1, then creates fresh col2 pointing at the same
        path.  col2 has never called _load_vectors(), so _vectors is None.
        embeddings_status() must still return the correct count from the .json sidecar.
        """
        embeddings_path = tmp_path / "embeddings"

        col1 = Collection(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col1.build_index()
        chunk_count = col1.build_embeddings()
        assert chunk_count > 0

        col2 = Collection(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col2.build_index()
        # Confirm vectors have NOT been loaded in-memory.
        assert col2._vectors is None

        status = col2.embeddings_status()

        assert status["available"] is True
        assert status["chunk_count"] == chunk_count


# ---------------------------------------------------------------------------
# build_embeddings() skip / force paths
# ---------------------------------------------------------------------------


class TestBuildEmbeddings:
    def test_build_embeddings_skip_when_already_built(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """build_embeddings(force=False) skips rebuild when index already has chunks.

        The first call embeds 9 chunks and saves to disk.  The second call (no
        force) must return the same count without re-embedding.
        """
        embeddings_path = tmp_path / "embeddings"
        col = Collection(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.build_index()
        count1 = col.build_embeddings()
        assert count1 == 9

        # Track embed calls to prove no re-embedding happens.
        original_embed = mock_provider.embed
        embed_calls: list = []

        def tracking_embed(texts):
            embed_calls.append(texts)
            return original_embed(texts)

        mock_provider.embed = tracking_embed  # type: ignore[method-assign]

        count2 = col.build_embeddings(force=False)

        # No new embedding calls; same count returned.
        assert embed_calls == []
        assert count2 == count1

    def test_build_embeddings_force_rebuilds(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """build_embeddings(force=True) re-embeds even when index exists."""
        embeddings_path = tmp_path / "embeddings"
        col = Collection(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.build_index()
        col.build_embeddings()

        original_embed = mock_provider.embed
        embed_calls: list = []

        def tracking_embed(texts):
            embed_calls.append(texts)
            return original_embed(texts)

        mock_provider.embed = tracking_embed  # type: ignore[method-assign]

        count = col.build_embeddings(force=True)

        # Re-embedding must have occurred.
        assert len(embed_calls) > 0
        assert count == 9

    def test_build_embeddings_uses_bounded_batches(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """build_embeddings() calls the provider in batches, not one giant call."""
        from markdown_vault_mcp.collection import _EMBEDDING_BATCH_SIZE

        embeddings_path = tmp_path / "embeddings"
        col = Collection(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.build_index()

        original_embed = mock_provider.embed
        batch_sizes: list[int] = []

        def tracking_embed(texts: list[str]) -> list[list[float]]:
            batch_sizes.append(len(texts))
            return original_embed(texts)

        mock_provider.embed = tracking_embed  # type: ignore[method-assign]

        count = col.build_embeddings(force=True)
        assert count == 9

        # embed() must have been called, and every batch at most _EMBEDDING_BATCH_SIZE.
        assert len(batch_sizes) > 0, "embed() should have been called"
        for size in batch_sizes:
            assert size <= _EMBEDDING_BATCH_SIZE

    def test_build_embeddings_multi_batch_corpus(
        self,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """build_embeddings() handles a corpus spanning multiple batches."""
        from markdown_vault_mcp.collection import _EMBEDDING_BATCH_SIZE

        # Create a vault with enough chunks to span multiple batches.
        vault = tmp_path / "vault"
        vault.mkdir()
        for i in range(_EMBEDDING_BATCH_SIZE + 10):
            (vault / f"note_{i:03d}.md").write_text(
                f"---\ntitle: Note {i}\n---\n\nContent of note {i}.\n"
            )

        embeddings_path = tmp_path / "embeddings"
        col = Collection(
            source_dir=vault,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
        )
        col.build_index()

        original_embed = mock_provider.embed
        batch_sizes: list[int] = []

        def tracking_embed(texts: list[str]) -> list[list[float]]:
            batch_sizes.append(len(texts))
            return original_embed(texts)

        mock_provider.embed = tracking_embed  # type: ignore[method-assign]

        count = col.build_embeddings(force=True)

        # All chunks embedded despite spanning multiple batches.
        assert count > _EMBEDDING_BATCH_SIZE
        # Multiple embed() calls must have been made.
        assert len(batch_sizes) > 1, "expected multiple embed() calls for large corpus"
        for size in batch_sizes:
            assert size <= _EMBEDDING_BATCH_SIZE
        # The .npy file must exist (saved at the end).
        npy_path = tmp_path / "embeddings.npy"
        assert npy_path.exists()


# ---------------------------------------------------------------------------
# build_index() second-call no-op
# ---------------------------------------------------------------------------


class TestBuildIndexNoOp:
    def test_second_build_index_is_noop(self, vault_path: Path) -> None:
        """build_index() a second time (no force) returns existing stats without re-scanning.

        The IndexStats returned on the second call must reflect the existing
        document count (9) while chunks_indexed=0 (no new scanning performed).
        """
        col = _make_collection(vault_path)
        col.build_index()

        # Intercept scan_directory to confirm it is NOT called again.
        import markdown_vault_mcp.collection as col_mod

        original_scan = col_mod.scan_directory
        scan_calls: list = []

        def tracking_scan(*args, **kwargs):
            scan_calls.append(args)
            return original_scan(*args, **kwargs)

        with patch.object(col_mod, "scan_directory", side_effect=tracking_scan):
            stats2 = col.build_index()

        # scan_directory must not have been invoked on the second call.
        assert scan_calls == []
        # Returned stats reflect the existing index content.
        assert stats2.documents_indexed == 9
        assert stats2.chunks_indexed == 0


# ---------------------------------------------------------------------------
# reindex() with vectors active
# ---------------------------------------------------------------------------


class TestReindexWithVectors:
    @pytest.fixture
    def writable_semantic(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> Collection:
        """Writable collection with vectors loaded in memory."""
        col = Collection(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
            state_path=tmp_path / "state.json",
            read_only=False,
        )
        col.build_index()
        col.build_embeddings()
        # Trigger vector load so _vectors is not None.
        col._load_vectors()
        return col

    def test_reindex_adds_vector_entries_for_new_files(
        self,
        writable_semantic: Collection,
        vault_path: Path,
    ) -> None:
        """reindex() embeds newly added files when the vector index is loaded."""
        before_count = writable_semantic._vectors.count  # type: ignore[union-attr]

        (vault_path / "brand_new.md").write_text(
            "# Brand New Note\n\nFresh content for reindex test.\n"
        )
        writable_semantic.reindex()

        after_count = writable_semantic._vectors.count  # type: ignore[union-attr]
        assert after_count == before_count + 1

        # The new file must be findable via semantic search.
        results = writable_semantic.search("fresh content", mode="semantic")
        paths = [r.path for r in results]
        assert "brand_new.md" in paths

    def test_reindex_removes_vector_entries_for_deleted_files(
        self,
        writable_semantic: Collection,
        vault_path: Path,
    ) -> None:
        """reindex() removes deleted files from the vector index."""
        before_count = writable_semantic._vectors.count  # type: ignore[union-attr]

        (vault_path / "simple.md").unlink()
        writable_semantic.reindex()

        after_count = writable_semantic._vectors.count  # type: ignore[union-attr]
        assert after_count == before_count - 1

        # Deleted file must not appear in semantic search results.
        results = writable_semantic.search("simple document", mode="semantic")
        paths = [r.path for r in results]
        assert "simple.md" not in paths

    def test_reindex_updates_vector_entries_for_modified_files(
        self,
        writable_semantic: Collection,
        vault_path: Path,
    ) -> None:
        """reindex() re-embeds modified files so updated content is searchable."""
        # simple.md contains "Simple Document"; replace with unique token.
        (vault_path / "simple.md").write_text(
            "# QuantumXyloscopeModified\n\nUnique modified content.\n"
        )
        writable_semantic.reindex()

        # Updated content must be findable by the new unique term.
        results = writable_semantic.search("QuantumXyloscopeModified", mode="semantic")
        paths = [r.path for r in results]
        assert "simple.md" in paths


# ---------------------------------------------------------------------------
# Gap coverage: _resolve_chunk_strategy
# ---------------------------------------------------------------------------


class TestResolveChunkStrategy:
    def test_whole_returns_whole_document_chunker(self) -> None:
        """_resolve_chunk_strategy('whole') returns a WholeDocumentChunker."""
        from markdown_vault_mcp.collection import _resolve_chunk_strategy
        from markdown_vault_mcp.scanner import WholeDocumentChunker

        result = _resolve_chunk_strategy("whole")
        assert isinstance(result, WholeDocumentChunker)

    def test_heading_returns_heading_chunker(self) -> None:
        """_resolve_chunk_strategy('heading') returns a HeadingChunker."""
        from markdown_vault_mcp.collection import _resolve_chunk_strategy
        from markdown_vault_mcp.scanner import HeadingChunker

        result = _resolve_chunk_strategy("heading")
        assert isinstance(result, HeadingChunker)

    def test_unknown_string_raises_value_error(self) -> None:
        """_resolve_chunk_strategy raises ValueError for unrecognised string."""
        from markdown_vault_mcp.collection import _resolve_chunk_strategy

        with pytest.raises(ValueError, match="Unknown chunk_strategy"):
            _resolve_chunk_strategy("paragraph")

    def test_strategy_instance_passes_through(self) -> None:
        """_resolve_chunk_strategy passes a ChunkStrategy instance through unchanged."""
        from markdown_vault_mcp.collection import _resolve_chunk_strategy
        from markdown_vault_mcp.scanner import WholeDocumentChunker

        strategy = WholeDocumentChunker()
        result = _resolve_chunk_strategy(strategy)
        assert result is strategy


# ---------------------------------------------------------------------------
# Gap coverage: _fts_row_to_note_info malformed JSON
# ---------------------------------------------------------------------------


class TestFtsRowToNoteInfoMalformedJson:
    def test_invalid_frontmatter_json_returns_empty_dict(self) -> None:
        """_fts_row_to_note_info with invalid JSON returns NoteInfo with empty frontmatter."""
        from markdown_vault_mcp.collection import _fts_row_to_note_info

        row = {
            "path": "x.md",
            "title": "X",
            "folder": "",
            "frontmatter_json": "not-valid-json{{{",
            "modified_at": 0.0,
        }
        result = _fts_row_to_note_info(row)

        assert isinstance(result, NoteInfo)
        assert result.frontmatter == {}
        assert result.path == "x.md"

    def test_none_frontmatter_json_returns_empty_dict(self) -> None:
        """_fts_row_to_note_info with frontmatter_json=None returns empty frontmatter."""
        from markdown_vault_mcp.collection import _fts_row_to_note_info

        row = {
            "path": "y.md",
            "title": "Y",
            "folder": "sub",
            "frontmatter_json": None,
            "modified_at": 1234567890.0,
        }
        result = _fts_row_to_note_info(row)

        assert result.frontmatter == {}

    def test_empty_string_frontmatter_json_returns_empty_dict(self) -> None:
        """_fts_row_to_note_info with frontmatter_json='' returns empty frontmatter."""
        from markdown_vault_mcp.collection import _fts_row_to_note_info

        row = {
            "path": "z.md",
            "title": "Z",
            "folder": "",
            "frontmatter_json": "",
            "modified_at": 0.0,
        }
        result = _fts_row_to_note_info(row)

        assert result.frontmatter == {}


# ---------------------------------------------------------------------------
# Gap coverage: read() error paths
# ---------------------------------------------------------------------------


class TestReadErrorPaths:
    def test_read_returns_none_for_path_traversal(self, collection: Collection) -> None:
        """read() returns None for paths that escape the source directory."""
        result = collection.read("../secret.md")
        assert result is None

    def test_read_returns_none_when_file_deleted_from_disk(
        self, vault_path: Path
    ) -> None:
        """read() returns None when file exists in index but was deleted from disk."""
        col = _make_collection(vault_path)
        col.build_index()

        # Confirm the file is readable before deleting it.
        assert col.read("simple.md") is not None

        # Delete the file directly from disk (bypassing the Collection API).
        (vault_path / "simple.md").unlink()

        # read() must return None — file no longer exists.
        result = col.read("simple.md")
        assert result is None


# ---------------------------------------------------------------------------
# Gap coverage: search() on document with no frontmatter
# ---------------------------------------------------------------------------


class TestSearchNoFrontmatter:
    def test_search_no_frontmatter_document_has_empty_frontmatter(
        self, collection: Collection
    ) -> None:
        """search() on a document with no frontmatter returns result.frontmatter == {}."""
        # no_frontmatter.md has no YAML header at all.
        results = collection.search("plain markdown", mode="keyword")
        no_fm_results = [r for r in results if r.path == "no_frontmatter.md"]

        assert len(no_fm_results) >= 1
        assert no_fm_results[0].frontmatter == {}


# ---------------------------------------------------------------------------
# Gap coverage: list() attachment edge cases
# ---------------------------------------------------------------------------


@pytest.fixture
def vault_with_root_attachment(vault_path: Path) -> Path:
    """Vault with an attachment at the root level (no subdirectory)."""
    (vault_path / "diagram.json").write_bytes(b'{"key": "value"}')
    (vault_path / "notes").mkdir(exist_ok=True)
    (vault_path / "notes" / "readme.txt").write_bytes(b"plain text file")
    return vault_path


class TestListAttachmentEdgeCases:
    def test_list_attachment_in_vault_root_has_empty_folder(
        self, vault_with_root_attachment: Path
    ) -> None:
        """Attachments at the vault root have folder='' (not '.' or '/')."""
        col = Collection(source_dir=vault_with_root_attachment)
        col.build_index()
        results = col.list(include_attachments=True)

        attachments = [r for r in results if isinstance(r, AttachmentInfo)]
        root_json = next((a for a in attachments if a.path == "diagram.json"), None)
        assert root_json is not None, "diagram.json not found in attachment listing"
        assert root_json.folder == ""

    def test_list_attachment_pattern_filters_by_extension(
        self, vault_with_root_attachment: Path
    ) -> None:
        """list(include_attachments=True, pattern='*.json') returns only .json files."""
        col = Collection(source_dir=vault_with_root_attachment)
        col.build_index()
        results = col.list(include_attachments=True, pattern="*.json")

        attachment_paths = [r.path for r in results if isinstance(r, AttachmentInfo)]
        assert all(p.endswith(".json") for p in attachment_paths)
        # txt file must not appear
        assert not any(p.endswith(".txt") for p in attachment_paths)

    def test_list_attachment_folder_filter_excludes_other_folders(
        self, vault_with_root_attachment: Path
    ) -> None:
        """list(include_attachments=True, folder='notes') only returns notes/ attachments."""
        col = Collection(source_dir=vault_with_root_attachment)
        col.build_index()
        results = col.list(include_attachments=True, folder="notes")

        for r in results:
            assert r.folder == "notes" or r.folder.startswith("notes/")

        # The root-level diagram.json must not appear.
        paths = [r.path for r in results]
        assert "diagram.json" not in paths


# ---------------------------------------------------------------------------
# Gap coverage: write_attachment() size limit
# ---------------------------------------------------------------------------


class TestWriteAttachmentSizeLimit:
    def test_write_attachment_raises_when_content_exceeds_limit(
        self, vault_path: Path
    ) -> None:
        """write_attachment() raises ValueError when content size exceeds the limit."""
        col = Collection(
            source_dir=vault_path,
            read_only=False,
            max_attachment_size_mb=0.000001,  # ~1 byte limit
        )
        with pytest.raises(ValueError, match="exceeds"):
            col.write_attachment("assets/big.pdf", b"a" * 100)

    def test_write_attachment_unlimited_when_max_is_zero(
        self, vault_path: Path
    ) -> None:
        """write_attachment() with max_attachment_size_mb=0 accepts any size."""
        col = Collection(
            source_dir=vault_path,
            read_only=False,
            max_attachment_size_mb=0,
        )
        large_content = b"x" * (20 * 1024 * 1024)  # 20 MB
        result = col.write_attachment("large_file.pdf", large_content)

        assert isinstance(result, WriteResult)
        assert result.path == "large_file.pdf"
        assert (vault_path / "large_file.pdf").stat().st_size == len(large_content)


# ---------------------------------------------------------------------------
# Attachment listing — hidden dir and exclude_patterns filtering (issue #78)
# ---------------------------------------------------------------------------


class TestListAttachmentHiddenDirFiltering:
    def test_hidden_dir_files_excluded_from_listing(self, tmp_path: Path) -> None:
        """list(include_attachments=True) excludes files inside hidden directories."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "doc.md").write_text("# Doc\n", encoding="utf-8")
        # Attachment in a normal directory — should appear.
        (vault / "assets").mkdir()
        (vault / "assets" / "diagram.pdf").write_bytes(b"PDF")
        # File in a hidden directory — must NOT appear.
        (vault / ".git").mkdir()
        (vault / ".git" / "config").write_bytes(b"[core]")
        # JSON file in .markdown_vault_mcp — must NOT appear.
        (vault / ".markdown_vault_mcp").mkdir()
        (vault / ".markdown_vault_mcp" / "state.json").write_text(
            "{}", encoding="utf-8"
        )

        col = Collection(source_dir=vault, attachment_extensions=["pdf", "json"])
        col.build_index()
        results = col.list(include_attachments=True)

        attachment_paths = {
            r.path
            for r in results
            if hasattr(r, "mime_type")  # AttachmentInfo
        }
        assert "assets/diagram.pdf" in attachment_paths
        assert not any(".git" in p for p in attachment_paths)
        assert not any(".markdown_vault_mcp" in p for p in attachment_paths)

    def test_dotfile_at_root_excluded(self, tmp_path: Path) -> None:
        """Files whose own name starts with '.' are not considered attachments."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "doc.md").write_text("# Doc\n", encoding="utf-8")
        # A dotfile directly in the vault root.
        (vault / ".hidden_config.json").write_bytes(b"{}")

        col = Collection(source_dir=vault, attachment_extensions=["json"])
        col.build_index()
        results = col.list(include_attachments=True)

        attachment_paths = {r.path for r in results if hasattr(r, "mime_type")}
        assert ".hidden_config.json" not in attachment_paths

    def test_exclude_patterns_applied_to_attachments(self, tmp_path: Path) -> None:
        """Configured exclude_patterns suppress attachments from excluded directories."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "doc.md").write_text("# Doc\n", encoding="utf-8")
        # File in a non-hidden excluded directory (tests pure exclude_patterns path).
        (vault / "archived").mkdir()
        (vault / "archived" / "workspace.json").write_text("{}", encoding="utf-8")
        # File in another excluded directory.
        (vault / "trash").mkdir()
        (vault / "trash" / "old.pdf").write_bytes(b"PDF")
        # File in a normal directory — should appear.
        (vault / "assets").mkdir()
        (vault / "assets" / "chart.pdf").write_bytes(b"PDF")

        col = Collection(
            source_dir=vault,
            attachment_extensions=["pdf", "json"],
            exclude_patterns=["archived/**", "trash/**"],
        )
        col.build_index()
        results = col.list(include_attachments=True)

        attachment_paths = {r.path for r in results if hasattr(r, "mime_type")}
        assert "assets/chart.pdf" in attachment_paths
        assert "trash/old.pdf" not in attachment_paths
        assert "archived/workspace.json" not in attachment_paths


# ---------------------------------------------------------------------------
# get_toc
# ---------------------------------------------------------------------------

# A document long enough to be split by HeadingChunker (default threshold 30
# lines). We need > 30 lines so that the chunker splits on headings rather than
# returning the whole file as a single NULL-headed chunk.
_LONG_DOC = """\
# Long Document Title

Introductory paragraph to pad the line count.
Line 04.
Line 05.
Line 06.
Line 07.
Line 08.
Line 09.
Line 10.

## Section Alpha

Content under Alpha.
Line 14.
Line 15.
Line 16.
Line 17.
Line 18.
Line 19.
Line 20.

## Section Beta

Content under Beta.
Line 24.
Line 25.
Line 26.
Line 27.
Line 28.
Line 29.
Line 30.
Line 31.
"""


@pytest.fixture
def collection_with_long_doc(vault_path: Path) -> Collection:
    """Collection with a long multi-section document added for ToC testing."""
    (vault_path / "long_doc.md").write_text(_LONG_DOC, encoding="utf-8")
    col = _make_collection(vault_path)
    col.build_index()
    return col


class TestCollectionGetToc:
    def test_get_toc_returns_headings(
        self, collection_with_long_doc: Collection
    ) -> None:
        """get_toc() returns headings for a document with multiple sections."""
        toc = collection_with_long_doc.get_toc("long_doc.md")

        assert isinstance(toc, list)
        assert len(toc) >= 2
        headings = [entry["heading"] for entry in toc]
        assert "Section Alpha" in headings
        # All entries must have heading and level keys.
        for entry in toc:
            assert "heading" in entry
            assert "level" in entry

    def test_get_toc_first_entry_is_synthetic_h1(
        self, collection_with_long_doc: Collection
    ) -> None:
        """The first entry in get_toc() is always the document title at level 1."""
        toc = collection_with_long_doc.get_toc("long_doc.md")

        assert toc[0]["level"] == 1
        assert toc[0]["heading"] == "Long Document Title"

    def test_get_toc_no_duplicate_h1(
        self, collection_with_long_doc: Collection
    ) -> None:
        """Synthetic H1 title must not duplicate a real H1 heading."""
        toc = collection_with_long_doc.get_toc("long_doc.md")

        h1_entries = [e for e in toc if e["level"] == 1]
        assert len(h1_entries) == 1, (
            f"Expected 1 H1, got {len(h1_entries)}: {h1_entries}"
        )
        assert h1_entries[0]["heading"] == "Long Document Title"

    def test_get_toc_raises_for_nonexistent_document(
        self, collection_with_long_doc: Collection
    ) -> None:
        """get_toc() raises ValueError for a path not in the index."""
        with pytest.raises(ValueError, match="Document not found"):
            collection_with_long_doc.get_toc("does_not_exist.md")


class TestReindexThreadSafety:
    """Verify that reindex() and write() do not corrupt the index when concurrent."""

    def test_reindex_and_write_concurrent(
        self, tmp_path: Path, vault_path: Path
    ) -> None:
        """Concurrent reindex + write does not crash or corrupt the FTS index."""
        state_path = tmp_path / "state.json"
        col = _make_collection(vault_path, state_path=state_path, read_only=False)
        col.build_index()

        # Add a file that reindex will discover.
        (vault_path / "concurrent_new.md").write_text(
            "# Concurrent\n\nNew file for reindex.\n"
        )

        def do_reindex() -> None:
            col.reindex()

        def do_write() -> None:
            col.write("written_during_reindex.md", "# Written\n\nBody.\n")

        # Run reindex and write concurrently — should not raise.
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(do_reindex), pool.submit(do_write)]
            for f in concurrent.futures.as_completed(futures):
                f.result()  # raises if the thread raised

        # Both documents must be readable — index is not corrupted.
        concurrent_note = col.read("concurrent_new.md")
        assert concurrent_note is not None, "reindex should have indexed the new file"
        written = col.read("written_during_reindex.md")
        assert written is not None, "write during reindex must persist"


# ---------------------------------------------------------------------------
# Optimistic concurrency (if_match)
# ---------------------------------------------------------------------------


class TestOptimisticConcurrency:
    """Tests for the if_match parameter on write operations."""

    # --- write() ---

    def test_write_with_correct_if_match_succeeds(
        self, writable: Collection, vault_path: Path
    ) -> None:
        """write() with a matching if_match etag succeeds."""
        path = "simple.md"
        current_etag = compute_file_hash(vault_path / path)

        result = writable.write(path, "# Updated\n\nNew body.\n", if_match=current_etag)

        assert result.created is False
        assert "Updated" in (vault_path / path).read_text()

    def test_write_with_wrong_if_match_raises(self, writable: Collection) -> None:
        """write() with a stale etag raises ConcurrentModificationError."""
        with pytest.raises(ConcurrentModificationError) as exc_info:
            writable.write("simple.md", "# Body\n", if_match="stale-etag-value")

        assert exc_info.value.path == "simple.md"
        assert exc_info.value.expected == "stale-etag-value"
        assert exc_info.value.actual != "stale-etag-value"

    def test_write_with_if_match_on_nonexistent_file_raises(
        self, writable: Collection
    ) -> None:
        """write() with if_match for a nonexistent file raises ConcurrentModificationError."""
        with pytest.raises(ConcurrentModificationError) as exc_info:
            writable.write("does_not_exist.md", "# Body\n", if_match="any-etag")

        assert exc_info.value.path == "does_not_exist.md"
        assert exc_info.value.actual == "(file does not exist)"

    def test_write_without_if_match_works_as_before(
        self, writable: Collection, vault_path: Path
    ) -> None:
        """write() without if_match performs unconditional write (backwards compatible)."""
        result = writable.write("simple.md", "# New Body\n\nContent.\n")

        assert result.created is False
        assert "New Body" in (vault_path / "simple.md").read_text()

    # --- edit() ---

    def test_edit_with_correct_if_match_succeeds(
        self, writable: Collection, vault_path: Path
    ) -> None:
        """edit() with a matching if_match etag succeeds."""
        path = "simple.md"
        current_etag = compute_file_hash(vault_path / path)
        file_content = (vault_path / path).read_text()
        old_text = file_content[:20]
        new_text = "CHANGED_PREFIX_TEXT_"

        result = writable.edit(path, old_text, new_text, if_match=current_etag)

        assert result.replacements == 1

    def test_edit_with_wrong_if_match_raises(self, writable: Collection) -> None:
        """edit() with a stale etag raises ConcurrentModificationError."""
        with pytest.raises(ConcurrentModificationError) as exc_info:
            writable.edit(
                "simple.md",
                "Simple Document",
                "Updated Document",
                if_match="stale-etag-value",
            )

        assert exc_info.value.path == "simple.md"
        assert exc_info.value.expected == "stale-etag-value"

    # --- delete() ---

    def test_delete_with_correct_if_match_succeeds(
        self, writable: Collection, vault_path: Path
    ) -> None:
        """delete() with a matching if_match etag removes the file."""
        path = "simple.md"
        current_etag = compute_file_hash(vault_path / path)

        result = writable.delete(path, if_match=current_etag)

        assert result.path == path
        assert not (vault_path / path).exists()

    def test_delete_with_wrong_if_match_raises(
        self, writable: Collection, vault_path: Path
    ) -> None:
        """delete() with a stale etag raises ConcurrentModificationError."""
        with pytest.raises(ConcurrentModificationError) as exc_info:
            writable.delete("simple.md", if_match="stale-etag-value")

        assert exc_info.value.path == "simple.md"
        # File must NOT have been deleted.
        assert (vault_path / "simple.md").exists()

    # --- rename() ---

    def test_rename_with_correct_if_match_succeeds(
        self, writable: Collection, vault_path: Path
    ) -> None:
        """rename() with a matching if_match etag renames the file."""
        old_path = "simple.md"
        new_path = "renamed_simple.md"
        current_etag = compute_file_hash(vault_path / old_path)

        result = writable.rename(old_path, new_path, if_match=current_etag)

        assert result.old_path == old_path
        assert result.new_path == new_path
        assert not (vault_path / old_path).exists()
        assert (vault_path / new_path).exists()

    def test_rename_with_wrong_if_match_raises(
        self, writable: Collection, vault_path: Path
    ) -> None:
        """rename() with a stale etag raises ConcurrentModificationError."""
        with pytest.raises(ConcurrentModificationError) as exc_info:
            writable.rename("simple.md", "renamed.md", if_match="stale-etag-value")

        assert exc_info.value.path == "simple.md"
        # File must NOT have been renamed.
        assert (vault_path / "simple.md").exists()
        assert not (vault_path / "renamed.md").exists()

    # --- write_attachment() ---

    def test_write_attachment_with_correct_if_match_succeeds(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() with a matching if_match etag overwrites the file."""
        col = Collection(source_dir=vault_with_attachment, read_only=False)
        att_path = vault_with_attachment / "assets" / "report.pdf"
        current_etag = compute_file_hash(att_path)
        new_content = b"updated PDF bytes"

        result = col.write_attachment(
            "assets/report.pdf", new_content, if_match=current_etag
        )

        assert result.created is False
        assert att_path.read_bytes() == new_content

    def test_write_attachment_with_wrong_if_match_raises(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() with a stale etag raises ConcurrentModificationError."""
        col = Collection(source_dir=vault_with_attachment, read_only=False)

        with pytest.raises(ConcurrentModificationError) as exc_info:
            col.write_attachment(
                "assets/report.pdf", b"new content", if_match="stale-etag-value"
            )

        assert exc_info.value.path == "assets/report.pdf"
        assert exc_info.value.expected == "stale-etag-value"

    # --- Round-trip test ---

    def test_read_etag_roundtrip_with_write(self, writable: Collection) -> None:
        """read() etag can be passed directly to write() as if_match and succeeds."""
        note = writable.read("simple.md")
        assert note is not None
        assert note.etag, "etag must be non-empty"

        result = writable.write(
            "simple.md", "# Round-trip\n\nBody.\n", if_match=note.etag
        )

        assert result.created is False

    def test_read_etag_roundtrip_with_edit(
        self, writable: Collection, vault_path: Path
    ) -> None:
        """read() etag can be passed to edit() as if_match and succeeds."""
        note = writable.read("simple.md")
        assert note is not None
        assert note.etag
        file_content = (vault_path / "simple.md").read_text()
        old_text = file_content[:15]

        result = writable.edit(
            "simple.md", old_text, "REPLACED_TEXT_X_", if_match=note.etag
        )

        assert result.replacements == 1

    def test_read_etag_stale_after_write(self, writable: Collection) -> None:
        """etag from read() is no longer valid after the file is modified."""
        note = writable.read("simple.md")
        assert note is not None
        stale_etag = note.etag

        # Modify the file.
        writable.write("simple.md", "# Modified\n\nNew content.\n")

        # Now the stale etag should fail.
        with pytest.raises(ConcurrentModificationError):
            writable.write("simple.md", "# Another write\n", if_match=stale_etag)


# ---------------------------------------------------------------------------
# Deferred embedding and callback tests (issue #175)
# ---------------------------------------------------------------------------


class TestDeferredEmbeddings:
    """Tests for the deferred embedding update mechanism."""

    def test_dirty_docs_flushed_on_semantic_search(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """Dirty documents are re-embedded when semantic_search is called."""
        col = Collection(
            source_dir=vault_path,
            embeddings_path=tmp_path / "embeddings",
            embedding_provider=mock_provider,
            read_only=False,
        )
        col.build_index()
        col.build_embeddings()

        col.write(
            "deferred_doc.md",
            "# Deferred Embedding\n\nUniqueContentForTest.\n",
        )
        # The dirty set should contain this path.
        assert "deferred_doc.md" in col._dirty_embeddings

        # Semantic search triggers flush.
        results = col.search("UniqueContentForTest", mode="semantic")
        paths = [r.path for r in results]
        assert "deferred_doc.md" in paths
        # Dirty set should now be empty.
        assert len(col._dirty_embeddings) == 0

    def test_dirty_docs_flushed_on_close(
        self,
        vault_path: Path,
        tmp_path: Path,
        mock_provider: MockEmbeddingProvider,
    ) -> None:
        """Dirty documents are re-embedded when close() is called."""
        embeddings_path = tmp_path / "embeddings"
        col = Collection(
            source_dir=vault_path,
            embeddings_path=embeddings_path,
            embedding_provider=mock_provider,
            read_only=False,
        )
        col.build_index()
        col.build_embeddings()

        col.write(
            "close_flush.md",
            "# Close Flush\n\nContent to be flushed on close.\n",
        )
        assert "close_flush.md" in col._dirty_embeddings

        col.close()
        assert len(col._dirty_embeddings) == 0

    def test_git_callback_fires_eventually(self, vault_path: Path) -> None:
        """Git callback fires in the background after write returns."""
        import threading

        event = threading.Event()
        calls: list = []

        def slow_callback(*args: object) -> None:
            calls.append(args)
            event.set()

        col = _make_collection(vault_path, read_only=False, on_write=slow_callback)
        col.build_index()

        col.write("bg_callback.md", "# Background\n\nTest.\n")

        # Callback hasn't fired yet (it's in a background thread).
        # Wait for it to complete.
        event.wait(timeout=5)
        assert len(calls) == 1
        _, _, operation = calls[0]
        assert operation == "write"
        col.close()


# ---------------------------------------------------------------------------
# Logging audit — previously-silent error paths now emit log messages (#182)
# ---------------------------------------------------------------------------


class TestLoggingAuditSilentPaths:
    """Verify that previously-silent except blocks now log at WARNING."""

    def test_list_attachments_stat_error_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """_list_attachments logs WARNING when stat() fails on a file."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "doc.md").write_text("# Doc\n", encoding="utf-8")
        att = vault / "data.csv"
        att.write_text("a,b\n1,2\n", encoding="utf-8")

        col = Collection(source_dir=vault, attachment_extensions=["csv"])
        col.build_index()

        # Delete the file after rglob discovers it but before stat().
        from pathlib import Path as _Path

        original_stat = _Path.stat
        seen_csv = False

        def stat_that_fails(self_path: _Path, *a: object, **kw: object) -> object:
            nonlocal seen_csv
            if self_path.name == "data.csv":
                if seen_csv:
                    raise OSError("simulated stat failure")
                seen_csv = True
            return original_stat(self_path, *a, **kw)

        with (
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.collection"),
            patch.object(_Path, "stat", stat_that_fails),
        ):
            results = col.list(include_attachments=True)
        attachment_paths = [r.path for r in results if isinstance(r, AttachmentInfo)]
        assert "data.csv" not in attachment_paths
        assert any("stat error" in rec.message for rec in caplog.records)

    def test_get_frontmatter_invalid_json_logs_warning(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Collection._get_frontmatter logs WARNING for invalid JSON."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "note.md").write_text("# Note\n", encoding="utf-8")
        col = Collection(source_dir=vault)
        col.build_index()

        bad_row = {
            "path": "note.md",
            "title": "Note",
            "folder": "",
            "frontmatter_json": "{broken-json",
            "modified_at": 0.0,
        }
        with (
            caplog.at_level(logging.WARNING, logger="markdown_vault_mcp.collection"),
            patch.object(col._fts, "get_note", return_value=bad_row),
        ):
            result = col._get_frontmatter("note.md")
        assert result == {}
        assert any(
            "_get_frontmatter: invalid JSON" in rec.message for rec in caplog.records
        )
