"""Unit tests for VectorIndex."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.vector_index import VectorIndex, VectorIndexCompatibilityError

if TYPE_CHECKING:
    from .conftest import MockEmbeddingProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_METADATA_KEYS = {"path", "title", "folder", "heading", "content"}


def _derive_folder(path: str) -> str:
    """Derive the folder string from a relative document path.

    Mirrors the logic in fts_index._derive_folder: parent directory of the
    path, with "." replaced by "" for root-level documents.

    Args:
        path: Relative document path (forward-slash separated).

    Returns:
        Parent directory string, or "" for root-level documents.
    """
    parent = Path(path).parent.as_posix()
    return "" if parent == "." else parent


def _make_meta(path: str, heading: str | None = None) -> dict:
    """Build a minimal metadata dict for testing.

    Args:
        path: Relative document path.
        heading: Optional section heading.

    Returns:
        Dict with all required metadata keys.
    """
    return {
        "path": path,
        "title": f"Title for {path}",
        "folder": _derive_folder(path),
        "heading": heading,
        "content": f"Content for {path} section {heading}",
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestVectorIndexAdd:
    def test_add_stores_embeddings(self, mock_provider: MockEmbeddingProvider) -> None:
        """Adding 3 texts results in count == 3."""
        index = VectorIndex(mock_provider)
        texts = ["alpha", "beta", "gamma"]
        meta = [_make_meta(f"doc{i}.md") for i in range(3)]

        added = index.add(texts, meta)

        assert added == 3
        assert index.count == 3

    def test_add_empty_list_is_noop(self, mock_provider: MockEmbeddingProvider) -> None:
        """Adding an empty list returns 0 and leaves count unchanged."""
        index = VectorIndex(mock_provider)
        result = index.add([], [])
        assert result == 0
        assert index.count == 0

    def test_add_mismatched_lengths_raises(
        self, mock_provider: MockEmbeddingProvider
    ) -> None:
        """Passing texts and metadata of different lengths raises ValueError."""
        index = VectorIndex(mock_provider)
        with pytest.raises(ValueError, match="same length"):
            index.add(["a", "b"], [_make_meta("x.md")])

    def test_multiple_adds_accumulate(
        self, mock_provider: MockEmbeddingProvider
    ) -> None:
        """Successive add() calls accumulate rows."""
        index = VectorIndex(mock_provider)
        index.add(["first"], [_make_meta("a.md")])
        index.add(["second", "third"], [_make_meta("b.md"), _make_meta("c.md")])
        assert index.count == 3


class TestVectorIndexSearch:
    def test_search_returns_results(self, mock_provider: MockEmbeddingProvider) -> None:
        """search() returns non-empty results with metadata and score after add()."""
        index = VectorIndex(mock_provider)
        texts = ["hello world", "foo bar", "something else"]
        meta = [_make_meta(f"note{i}.md") for i in range(3)]
        index.add(texts, meta)

        results = index.search("hello world")

        assert len(results) > 0
        first = results[0]
        assert "score" in first
        assert isinstance(first["score"], float)
        # All required metadata keys should be present.
        for key in _METADATA_KEYS:
            assert key in first

    def test_search_ranking(self, mock_provider: MockEmbeddingProvider) -> None:
        """The text that matches the query scores highest."""
        index = VectorIndex(mock_provider)
        target_text = "machine learning algorithms"
        other_texts = ["cooking recipes", "travel destinations", "sports news"]
        all_texts = [target_text, *other_texts]
        meta = [_make_meta(f"doc{i}.md") for i in range(len(all_texts))]
        index.add(all_texts, meta)

        results = index.search(target_text, limit=len(all_texts))

        assert len(results) > 0
        # The exact same text embedded should score highest.
        assert results[0]["content"] == "Content for doc0.md section None"

    def test_search_empty_index_returns_empty(
        self, mock_provider: MockEmbeddingProvider
    ) -> None:
        """Searching an empty index returns an empty list."""
        index = VectorIndex(mock_provider)
        results = index.search("anything")
        assert results == []

    def test_search_limit_respected(self, mock_provider: MockEmbeddingProvider) -> None:
        """search() returns at most limit results."""
        index = VectorIndex(mock_provider)
        texts = [f"document {i}" for i in range(10)]
        meta = [_make_meta(f"doc{i}.md") for i in range(10)]
        index.add(texts, meta)

        results = index.search("document", limit=3)

        assert len(results) <= 3

    def test_metadata_preserved(self, mock_provider: MockEmbeddingProvider) -> None:
        """All metadata fields are preserved and returned in search results."""
        index = VectorIndex(mock_provider)
        specific_meta = {
            "path": "journal/2024.md",
            "title": "My Journal",
            "folder": "journal",
            "heading": "Introduction",
            "content": "This is the intro section",
        }
        index.add(["This is the intro section"], [specific_meta])

        results = index.search("intro section")

        assert len(results) == 1
        result = results[0]
        assert result["path"] == "journal/2024.md"
        assert result["title"] == "My Journal"
        assert result["folder"] == "journal"
        assert result["heading"] == "Introduction"
        assert result["content"] == "This is the intro section"
        assert "score" in result


class TestVectorIndexDelete:
    def test_delete_by_path_reduces_count(
        self, mock_provider: MockEmbeddingProvider
    ) -> None:
        """delete_by_path() removes rows and decrements count."""
        index = VectorIndex(mock_provider)
        index.add(
            ["alpha", "beta", "gamma"],
            [_make_meta("a.md"), _make_meta("b.md"), _make_meta("c.md")],
        )

        removed = index.delete_by_path("b.md")

        assert removed == 1
        assert index.count == 2

    def test_delete_by_path_removes_from_search(
        self, mock_provider: MockEmbeddingProvider
    ) -> None:
        """Deleted document's path does not appear in subsequent search results."""
        index = VectorIndex(mock_provider)
        index.add(
            ["alpha content", "beta content"],
            [_make_meta("a.md"), _make_meta("b.md")],
        )
        index.delete_by_path("a.md")

        results = index.search("alpha content", limit=10)

        paths = [r["path"] for r in results]
        assert "a.md" not in paths

    def test_delete_all_chunks_for_path(
        self, mock_provider: MockEmbeddingProvider
    ) -> None:
        """delete_by_path() removes all chunks belonging to the given path."""
        index = VectorIndex(mock_provider)
        # Three chunks for "multi.md", one for "other.md".
        index.add(
            ["chunk one", "chunk two", "chunk three", "other content"],
            [
                _make_meta("multi.md", heading="Section 1"),
                _make_meta("multi.md", heading="Section 2"),
                _make_meta("multi.md", heading="Section 3"),
                _make_meta("other.md"),
            ],
        )

        removed = index.delete_by_path("multi.md")

        assert removed == 3
        assert index.count == 1

    def test_delete_nonexistent_path_returns_zero(
        self, mock_provider: MockEmbeddingProvider
    ) -> None:
        """delete_by_path() on a path not in the index returns 0."""
        index = VectorIndex(mock_provider)
        index.add(["content"], [_make_meta("a.md")])

        removed = index.delete_by_path("nonexistent.md")

        assert removed == 0
        assert index.count == 1

    def test_delete_only_path_empties_index(
        self, mock_provider: MockEmbeddingProvider
    ) -> None:
        """Deleting the only path resets the index to empty."""
        index = VectorIndex(mock_provider)
        index.add(["solo"], [_make_meta("solo.md")])

        index.delete_by_path("solo.md")

        assert index.count == 0
        # Search on empty index should not crash.
        assert index.search("solo") == []


class TestVectorIndexPersistence:
    def test_save_load_roundtrip(
        self, mock_provider: MockEmbeddingProvider, tmp_path: Path
    ) -> None:
        """Saving and loading preserves count and search results."""
        index = VectorIndex(mock_provider)
        index.add(
            ["north star", "southern cross", "big dipper"],
            [
                _make_meta("stars.md", heading="North"),
                _make_meta("stars.md", heading="South"),
                _make_meta("stars.md", heading="Other"),
            ],
        )

        base = tmp_path / "embeddings"
        index.save(base)

        loaded = VectorIndex.load(base, mock_provider)

        assert loaded.count == index.count
        results = loaded.search("north star", limit=3)
        assert len(results) > 0
        paths = {r["path"] for r in results}
        assert "stars.md" in paths

    def test_save_empty_index(
        self, mock_provider: MockEmbeddingProvider, tmp_path: Path
    ) -> None:
        """Saving and loading an empty index does not crash."""
        index = VectorIndex(mock_provider)
        base = tmp_path / "empty_embeddings"

        index.save(base)

        loaded = VectorIndex.load(base, mock_provider)
        assert loaded.count == 0
        assert loaded.search("anything") == []

    def test_save_creates_sidecar_files(
        self, mock_provider: MockEmbeddingProvider, tmp_path: Path
    ) -> None:
        """save() writes both .npy and .json sidecar files."""
        index = VectorIndex(mock_provider)
        index.add(["hello"], [_make_meta("hello.md")])
        base = tmp_path / "idx"

        index.save(base)

        assert (tmp_path / "idx.npy").exists()
        assert (tmp_path / "idx.json").exists()

    def test_load_missing_file_raises(
        self, mock_provider: MockEmbeddingProvider, tmp_path: Path
    ) -> None:
        """load() raises FileNotFoundError when sidecar files are missing."""
        base = tmp_path / "nonexistent"
        with pytest.raises(FileNotFoundError):
            VectorIndex.load(base, mock_provider)

    def test_save_load_metadata_integrity(
        self, mock_provider: MockEmbeddingProvider, tmp_path: Path
    ) -> None:
        """All metadata fields survive a save/load cycle unchanged."""
        original_meta = {
            "path": "projects/report.md",
            "title": "Annual Report",
            "folder": "projects",
            "heading": "Executive Summary",
            "content": "Key findings from the year",
        }
        index = VectorIndex(mock_provider)
        index.add(["Key findings from the year"], [original_meta])

        base = tmp_path / "meta_check"
        index.save(base)
        loaded = VectorIndex.load(base, mock_provider)

        results = loaded.search("Key findings", limit=1)
        assert len(results) == 1
        result = results[0]
        for key, expected in original_meta.items():
            assert result[key] == expected

    def test_load_legacy_json_list_payload(
        self, mock_provider: MockEmbeddingProvider, tmp_path: Path
    ) -> None:
        """load() accepts legacy JSON list metadata format."""
        index = VectorIndex(mock_provider)
        index.add(["legacy"], [_make_meta("legacy.md")])
        base = tmp_path / "legacy_idx"
        index.save(base)

        legacy_payload = [_make_meta("legacy.md")]
        with (tmp_path / "legacy_idx.json").open("w", encoding="utf-8") as fh:
            json.dump(legacy_payload, fh)

        loaded = VectorIndex.load(base, mock_provider)
        assert loaded.count == 1

    def test_load_raises_on_provider_model_mismatch(
        self, mock_provider: MockEmbeddingProvider, tmp_path: Path
    ) -> None:
        """load() raises compatibility error when provider/model identity differs."""
        index = VectorIndex(mock_provider)
        index.add(["alpha"], [_make_meta("alpha.md")])
        base = tmp_path / "compat_idx"
        index.save(base)

        json_path = tmp_path / "compat_idx.json"
        with json_path.open(encoding="utf-8") as fh:
            payload = json.load(fh)
        payload["index_metadata"]["provider"] = "different-provider"
        with json_path.open("w", encoding="utf-8") as fh:
            json.dump(payload, fh)

        with pytest.raises(VectorIndexCompatibilityError, match="mismatch"):
            VectorIndex.load(base, mock_provider)


class TestSearchByPath:
    """Tests for VectorIndex.search_by_path."""

    def test_returns_similar_from_other_docs(
        self, mock_provider: MockEmbeddingProvider
    ) -> None:
        """search_by_path returns chunks from other documents, not self."""
        index = VectorIndex(mock_provider)
        index.add(
            ["doc A content", "doc B content", "doc C content"],
            [_make_meta("a.md"), _make_meta("b.md"), _make_meta("c.md")],
        )
        results = index.search_by_path("a.md", limit=5)
        paths = {r["path"] for r in results}
        assert "a.md" not in paths
        assert len(results) == 2  # b.md and c.md

    def test_returns_empty_for_nonexistent_path(
        self, mock_provider: MockEmbeddingProvider
    ) -> None:
        """search_by_path returns [] if path has no stored embeddings."""
        index = VectorIndex(mock_provider)
        index.add(["content"], [_make_meta("a.md")])
        assert index.search_by_path("nonexistent.md") == []

    def test_returns_empty_for_empty_index(
        self, mock_provider: MockEmbeddingProvider
    ) -> None:
        """search_by_path returns [] on empty index."""
        index = VectorIndex(mock_provider)
        assert index.search_by_path("any.md") == []

    def test_multi_chunk_averaging(self, mock_provider: MockEmbeddingProvider) -> None:
        """Document with multiple chunks uses mean vector for similarity."""
        index = VectorIndex(mock_provider)
        # doc_a has 2 chunks, doc_b has 1
        index.add(
            ["chunk1 of a", "chunk2 of a", "content of b"],
            [
                _make_meta("a.md", "H1"),
                _make_meta("a.md", "H2"),
                _make_meta("b.md"),
            ],
        )
        results = index.search_by_path("a.md", limit=5)
        assert len(results) == 1
        assert results[0]["path"] == "b.md"
        assert "score" in results[0]

    def test_respects_limit(self, mock_provider: MockEmbeddingProvider) -> None:
        """search_by_path respects the limit parameter."""
        index = VectorIndex(mock_provider)
        texts = [f"doc {i}" for i in range(5)]
        meta = [_make_meta(f"doc{i}.md") for i in range(5)]
        index.add(texts, meta)
        results = index.search_by_path("doc0.md", limit=2)
        assert len(results) == 2
