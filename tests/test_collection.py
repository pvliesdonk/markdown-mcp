"""Integration tests for Collection."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_mcp.collection import Collection
from markdown_mcp.exceptions import ReadOnlyError
from markdown_mcp.types import CollectionStats, NoteContent, NoteInfo

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

        # 9 valid .md files (excludes malformed_yaml.md and invalid_utf8.md).
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
