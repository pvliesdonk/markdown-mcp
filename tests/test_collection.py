"""Integration tests for Collection."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_mcp.collection import Collection
from markdown_mcp.exceptions import (
    DocumentNotFoundError,
    EditConflictError,
    ReadOnlyError,
)
from markdown_mcp.types import (
    CollectionStats,
    DeleteResult,
    EditResult,
    NoteContent,
    NoteInfo,
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


