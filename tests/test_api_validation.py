"""API validation tests: verify Collection works with ifcraftcorpus settings.

Phase 1 gate — confirms that required_frontmatter exclusion, tag filtering,
list_tags, and stats all behave correctly before proceeding to Phase 2.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.collection import Collection

if TYPE_CHECKING:
    from pathlib import Path

    from markdown_vault_mcp.types import IndexStats


# ---------------------------------------------------------------------------
# Corpus fixture content
# ---------------------------------------------------------------------------

_EXEMPLAR1 = """\
---
title: The Haunted Manor
cluster: fiction
topics:
  - horror
  - gothic
  - haunted-house
---

# The Haunted Manor

A dark and stormy night at the old manor house. The doors creaked
with every gust of wind, and shadows danced along the walls.

## Chapter One

The protagonist arrived at midnight, carrying nothing but a lantern.
"""

_EXEMPLAR2 = """\
---
title: Space Explorer's Guide
cluster: nonfiction
topics:
  - science
  - space
  - exploration
---

# Space Explorer's Guide

A comprehensive guide to navigating the cosmos. From basic astronomy
to advanced navigation techniques.

## Navigation Basics

Stars have been used for navigation since ancient times.
"""

_EXEMPLAR3 = """\
---
title: Gothic Tales Collection
cluster: fiction
topics:
  - gothic
  - anthology
---

# Gothic Tales Collection

An anthology of gothic short stories spanning two centuries.
"""

_INCOMPLETE = """\
---
title: Incomplete Entry
---

This document has a title but no cluster field.
"""

_NO_FRONTMATTER = """\
A plain document with no frontmatter at all.
"""


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def corpus_path(tmp_path: Path) -> Path:
    """Write corpus documents to tmp_path/corpus and return the corpus directory.

    Three documents satisfy required_frontmatter=["title", "cluster"]:
        exemplar1.md, exemplar2.md, exemplar3.md

    Two documents are excluded:
        incomplete.md  -- has title but no cluster
        no_frontmatter.md  -- has neither field
    """
    corpus = tmp_path / "corpus"
    corpus.mkdir()

    (corpus / "exemplar1.md").write_text(_EXEMPLAR1, encoding="utf-8")
    (corpus / "exemplar2.md").write_text(_EXEMPLAR2, encoding="utf-8")
    (corpus / "exemplar3.md").write_text(_EXEMPLAR3, encoding="utf-8")
    (corpus / "incomplete.md").write_text(_INCOMPLETE, encoding="utf-8")
    (corpus / "no_frontmatter.md").write_text(_NO_FRONTMATTER, encoding="utf-8")

    return corpus


@pytest.fixture
def corpus_collection(corpus_path: Path) -> tuple[Collection, IndexStats]:
    """Return a built Collection configured with ifcraftcorpus settings.

    Returns:
        Tuple of (collection, index_stats) so tests can inspect both.
    """
    collection = Collection(
        source_dir=corpus_path,
        required_frontmatter=["title", "cluster"],
        indexed_frontmatter_fields=["cluster", "topics"],
        read_only=True,
    )
    stats = collection.build_index()
    return collection, stats


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRequiredFrontmatter:
    def test_required_frontmatter_excludes_incomplete(
        self, corpus_collection: tuple[Collection, IndexStats]
    ) -> None:
        """Only 3 documents are indexed; incomplete.md and no_frontmatter.md are skipped."""
        collection, _ = corpus_collection
        s = collection.stats()
        assert s.document_count == 3

    def test_stats_skipped_count(
        self, corpus_collection: tuple[Collection, IndexStats]
    ) -> None:
        """build_index() reports skipped == 2 (incomplete + no_frontmatter)."""
        _, index_stats = corpus_collection
        assert index_stats.skipped == 2


class TestSearchWithFilters:
    def test_search_with_cluster_filter(
        self, corpus_collection: tuple[Collection, IndexStats]
    ) -> None:
        """search(filters={"cluster": "nonfiction"}) returns only exemplar2."""
        collection, _ = corpus_collection
        results = collection.search("guide", filters={"cluster": "nonfiction"})

        assert len(results) == 1
        assert results[0].path == "exemplar2.md"

    def test_search_with_fiction_filter(
        self, corpus_collection: tuple[Collection, IndexStats]
    ) -> None:
        """search(filters={"cluster": "fiction"}) returns only fiction docs."""
        collection, _ = corpus_collection
        results = collection.search("gothic", filters={"cluster": "fiction"})

        paths = {r.path for r in results}
        # Keyword search for "gothic" only matches exemplar3.md (gothic in
        # title + body).  exemplar1.md has gothic only in frontmatter topics,
        # which is not in the FTS5 virtual table.
        assert paths == {"exemplar3.md"}, f"Unexpected paths in results: {paths}"

    def test_multi_tag_filter(
        self, corpus_collection: tuple[Collection, IndexStats]
    ) -> None:
        """search with cluster=fiction AND topics=gothic returns only matching docs."""
        collection, _ = corpus_collection
        results = collection.search(
            "gothic",
            filters={"cluster": "fiction", "topics": "gothic"},
        )

        paths = {r.path for r in results}
        # Keyword search for "gothic" only hits exemplar3.md (gothic in FTS
        # content).  The filters further restrict to cluster=fiction AND
        # topics=gothic, which exemplar3 satisfies.
        assert paths == {"exemplar3.md"}, f"Unexpected paths in results: {paths}"


class TestListTags:
    def test_list_tags_cluster(
        self, corpus_collection: tuple[Collection, IndexStats]
    ) -> None:
        """list_tags("cluster") returns ["fiction", "nonfiction"] (sorted)."""
        collection, _ = corpus_collection
        clusters = collection.list_tags("cluster")
        assert clusters == ["fiction", "nonfiction"]

    def test_list_tags_topics(
        self, corpus_collection: tuple[Collection, IndexStats]
    ) -> None:
        """list_tags("topics") returns all distinct topic values from the 3 indexed docs."""
        collection, _ = corpus_collection
        topics = collection.list_tags("topics")

        # Topics from the three indexed documents:
        #   exemplar1: horror, gothic, haunted-house
        #   exemplar2: science, space, exploration
        #   exemplar3: gothic, anthology
        expected = sorted(
            {
                "horror",
                "gothic",
                "haunted-house",
                "science",
                "space",
                "exploration",
                "anthology",
            }
        )
        assert topics == expected

    def test_list_tags_unindexed_field(
        self, corpus_collection: tuple[Collection, IndexStats]
    ) -> None:
        """list_tags("author") returns [] — author is not in indexed_frontmatter_fields."""
        collection, _ = corpus_collection
        result = collection.list_tags("author")
        assert result == []


class TestStats:
    def test_stats_indexed_fields(
        self, corpus_collection: tuple[Collection, IndexStats]
    ) -> None:
        """stats().indexed_frontmatter_fields reports ["cluster", "topics"]."""
        collection, _ = corpus_collection
        s = collection.stats()
        assert sorted(s.indexed_frontmatter_fields) == ["cluster", "topics"]


class TestFrontmatterInResults:
    def test_search_returns_frontmatter(
        self, corpus_collection: tuple[Collection, IndexStats]
    ) -> None:
        """Search results include the correct frontmatter dict for the matched document."""
        collection, _ = corpus_collection
        results = collection.search("guide", filters={"cluster": "nonfiction"})

        assert len(results) == 1
        fm = results[0].frontmatter
        assert fm.get("title") == "Space Explorer's Guide"
        assert fm.get("cluster") == "nonfiction"
        assert "science" in fm.get("topics", [])
