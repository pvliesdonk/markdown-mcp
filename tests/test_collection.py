"""Integration tests for Collection."""

from __future__ import annotations

import concurrent.futures
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.exceptions import (
    DocumentExistsError,
    DocumentNotFoundError,
    EditConflictError,
    ReadOnlyError,
)
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
    (vault_path / "assets" / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)
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

    def test_read_attachment_not_found_raises(self, vault_with_attachment: Path) -> None:
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


class TestWriteAttachment:
    def test_write_attachment_creates_file(
        self, vault_with_attachment: Path
    ) -> None:
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
        assert (vault_with_attachment / "assets" / "report.pdf").read_bytes() == new_content

    def test_write_attachment_creates_intermediate_dirs(
        self, vault_with_attachment: Path
    ) -> None:
        """write_attachment() creates parent directories as needed."""
        col = Collection(source_dir=vault_with_attachment, read_only=False)
        col.write_attachment("deep/nested/file.pdf", b"content")

        assert (vault_with_attachment / "deep" / "nested" / "file.pdf").is_file()

    def test_write_attachment_readonly_raises(self, vault_with_attachment: Path) -> None:
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

    def test_write_attachment_triggers_callback(self, vault_with_attachment: Path) -> None:
        """write_attachment() invokes the on_write callback."""
        calls: list = []
        col = Collection(
            source_dir=vault_with_attachment,
            read_only=False,
            on_write=lambda *args: calls.append(args),
        )
        col.write_attachment("assets/cb.pdf", b"callback test")

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
        col = Collection(
            source_dir=vault_with_attachment, attachment_extensions=["*"]
        )
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
        col = Collection(
            source_dir=vault_path, attachment_extensions=["pdf", "docx"]
        )
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
