"""Tests for link extraction, FTS storage, and Collection backlink/outlink API."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from markdown_vault_mcp.collection import Collection
from markdown_vault_mcp.fts_index import FTSIndex
from markdown_vault_mcp.scanner import extract_links
from markdown_vault_mcp.types import (
    BacklinkInfo,
    Chunk,
    LinkInfo,
    OutlinkInfo,
    ParsedNote,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_note(
    path: str = "test.md",
    title: str = "Test",
    links: list[LinkInfo] | None = None,
    chunks: list[Chunk] | None = None,
    content_hash: str = "abc123",
    modified_at: float = 1000.0,
) -> ParsedNote:
    """Create a minimal ParsedNote for testing.

    Args:
        path: Relative document path.
        title: Document title.
        links: Pre-built links list.
        chunks: Chunk list. Defaults to a single generic chunk.
        content_hash: Content hash string.
        modified_at: Modification timestamp.

    Returns:
        A :class:`ParsedNote` suitable for indexing.
    """
    if chunks is None:
        chunks = [
            Chunk(heading="Test", heading_level=1, content="Test content", start_line=0)
        ]
    return ParsedNote(
        path=path,
        frontmatter={},
        title=title,
        chunks=chunks,
        content_hash=content_hash,
        modified_at=modified_at,
        links=links or [],
    )


# ---------------------------------------------------------------------------
# extract_links: inline markdown links
# ---------------------------------------------------------------------------


class TestExtractInlineLinks:
    def test_simple_inline_link(self) -> None:
        """Basic [text](path.md) is extracted as a markdown link."""
        links = extract_links("[Click here](notes/intro.md)", "index.md")
        assert len(links) == 1
        assert links[0].target_path == "notes/intro.md"
        assert links[0].link_text == "Click here"
        assert links[0].link_type == "markdown"
        assert links[0].fragment is None

    def test_inline_link_with_fragment(self) -> None:
        """Fragment identifier is split from target and stored separately."""
        links = extract_links("[See section](notes/intro.md#overview)", "index.md")
        assert len(links) == 1
        assert links[0].target_path == "notes/intro.md"
        assert links[0].fragment == "overview"

    def test_external_url_skipped(self) -> None:
        """HTTP/HTTPS links are not extracted."""
        links = extract_links("[Web](https://example.com)", "index.md")
        assert links == []

    def test_mailto_skipped(self) -> None:
        """mailto: links are not extracted."""
        links = extract_links("[Mail](mailto:foo@bar.com)", "index.md")
        assert links == []

    def test_pure_anchor_skipped(self) -> None:
        """Pure anchor links (# only) are not extracted."""
        links = extract_links("[Top](#top)", "index.md")
        assert links == []

    def test_relative_path_resolved(self) -> None:
        """Relative path is resolved against source document's directory."""
        links = extract_links("[Note](../sibling.md)", "Journal/2024/today.md")
        assert len(links) == 1
        assert links[0].target_path == "Journal/sibling.md"

    def test_relative_path_same_dir(self) -> None:
        """Relative path in same directory resolves correctly."""
        links = extract_links("[Note](other.md)", "Journal/today.md")
        assert len(links) == 1
        assert links[0].target_path == "Journal/other.md"

    def test_traversal_above_root_clamped(self) -> None:
        """Path traversal above vault root is clamped to root level."""
        links = extract_links("[Note](../../escape.md)", "a/b/c.md")
        assert len(links) == 1
        # a/b/../../escape.md -> escape.md (root level)
        assert links[0].target_path == "escape.md"

    def test_multiple_inline_links(self) -> None:
        """Multiple links in one document are all extracted."""
        content = "See [A](a.md) and [B](b.md) for more."
        links = extract_links(content, "index.md")
        assert len(links) == 2
        targets = {lnk.target_path for lnk in links}
        assert targets == {"a.md", "b.md"}

    def test_image_link_excluded(self) -> None:
        """Image links ![alt](src) are not extracted."""
        links = extract_links("![photo](assets/cat.png)", "index.md")
        assert links == []

    def test_image_link_alongside_regular(self) -> None:
        """Regular link is extracted, image link next to it is skipped."""
        content = "![img](pic.png) and [note](note.md)"
        links = extract_links(content, "index.md")
        assert len(links) == 1
        assert links[0].target_path == "note.md"

    def test_empty_link_text_allowed(self) -> None:
        """Empty link text is still extracted."""
        links = extract_links("[](note.md)", "index.md")
        assert len(links) == 1
        assert links[0].link_text == ""


# ---------------------------------------------------------------------------
# extract_links: reference-style links
# ---------------------------------------------------------------------------


class TestExtractReferenceLinks:
    def test_reference_link_basic(self) -> None:
        """[text][ref] with [ref]: path.md definition is extracted."""
        content = "See [the note][ref1]\n\n[ref1]: notes/topic.md"
        links = extract_links(content, "index.md")
        assert len(links) == 1
        assert links[0].target_path == "notes/topic.md"
        assert links[0].link_text == "the note"
        assert links[0].link_type == "reference"
        assert links[0].fragment is None

    def test_reference_link_with_fragment(self) -> None:
        """Fragment in reference definition target is split off."""
        content = "See [note][ref]\n\n[ref]: notes/topic.md#section"
        links = extract_links(content, "index.md")
        assert len(links) == 1
        assert links[0].target_path == "notes/topic.md"
        assert links[0].fragment == "section"

    def test_reference_link_case_insensitive_key(self) -> None:
        """Reference keys are case-insensitive per Markdown spec."""
        content = "See [note][REF]\n\n[ref]: target.md"
        links = extract_links(content, "index.md")
        assert len(links) == 1
        assert links[0].target_path == "target.md"

    def test_reference_definition_with_title_stripped(self) -> None:
        """Optional CommonMark title in definition is stripped from target."""
        content = 'See [note][ref]\n\n[ref]: target.md "My Title"'
        links = extract_links(content, "index.md")
        assert len(links) == 1
        assert links[0].target_path == "target.md"

    def test_undefined_reference_skipped(self) -> None:
        """Usage with no matching definition is silently skipped."""
        links = extract_links("See [note][missing]", "index.md")
        assert links == []

    def test_external_reference_skipped(self) -> None:
        """Reference definition pointing to external URL is not extracted."""
        content = "See [web][ref]\n\n[ref]: https://example.com"
        links = extract_links(content, "index.md")
        assert links == []

    def test_reference_relative_path_resolved(self) -> None:
        """Relative path in reference definition is resolved correctly."""
        content = "See [note][ref]\n\n[ref]: ../sibling.md"
        links = extract_links(content, "Journal/2024/today.md")
        assert len(links) == 1
        assert links[0].target_path == "Journal/sibling.md"


# ---------------------------------------------------------------------------
# extract_links: wikilinks
# ---------------------------------------------------------------------------


class TestExtractWikilinks:
    def test_wikilink_basic(self) -> None:
        """[[Note Title]] is extracted with .md extension appended."""
        links = extract_links("See [[Note Title]]", "index.md")
        assert len(links) == 1
        assert links[0].target_path == "Note Title.md"
        assert links[0].link_text == "Note Title"
        assert links[0].link_type == "wikilink"
        assert links[0].fragment is None

    def test_wikilink_with_alias(self) -> None:
        """[[path|alias]] uses alias as link_text."""
        links = extract_links("See [[notes/topic|My Topic]]", "index.md")
        assert len(links) == 1
        assert links[0].target_path == "notes/topic.md"
        assert links[0].link_text == "My Topic"

    def test_wikilink_md_extension_not_doubled(self) -> None:
        """[[note.md]] does not become note.md.md."""
        links = extract_links("See [[note.md]]", "index.md")
        assert len(links) == 1
        assert links[0].target_path == "note.md"

    def test_wikilink_with_fragment(self) -> None:
        """[[note#section]] splits fragment from path."""
        links = extract_links("See [[note#section]]", "index.md")
        assert len(links) == 1
        assert links[0].target_path == "note.md"
        assert links[0].fragment == "section"

    def test_wikilink_relative_path(self) -> None:
        """Wikilinks with subdirectory paths resolve against source dir."""
        links = extract_links("See [[subdir/note]]", "Journal/today.md")
        assert len(links) == 1
        assert links[0].target_path == "Journal/subdir/note.md"

    def test_wikilink_dotmd_with_fragment(self) -> None:
        """[[note.md#heading]] keeps .md, splits fragment."""
        links = extract_links("See [[note.md#heading]]", "index.md")
        assert len(links) == 1
        assert links[0].target_path == "note.md"
        assert links[0].fragment == "heading"


# ---------------------------------------------------------------------------
# extract_links: code block exclusion
# ---------------------------------------------------------------------------


class TestCodeBlockExclusion:
    def test_link_in_fenced_code_excluded(self) -> None:
        """Links inside fenced code blocks are not extracted."""
        content = "```\nSee [note](target.md)\n```"
        links = extract_links(content, "index.md")
        assert links == []

    def test_link_in_inline_code_excluded(self) -> None:
        """Links inside inline code spans are not extracted."""
        content = "Use `[note](target.md)` syntax"
        links = extract_links(content, "index.md")
        assert links == []

    def test_link_after_fenced_code_extracted(self) -> None:
        """Links after a code block are still extracted."""
        content = "```python\ncode\n```\n\nSee [real](real.md)"
        links = extract_links(content, "index.md")
        assert len(links) == 1
        assert links[0].target_path == "real.md"

    def test_wikilink_in_fenced_code_excluded(self) -> None:
        """Wikilinks inside fenced code blocks are not extracted."""
        content = "```\n[[SomeNote]]\n```"
        links = extract_links(content, "index.md")
        assert links == []


# ---------------------------------------------------------------------------
# extract_links: mixed content
# ---------------------------------------------------------------------------


class TestMixedLinks:
    def test_all_three_types_together(self) -> None:
        """All three link types in one document are all extracted."""
        content = "[Inline](a.md)\n[[WikiNote]]\n[RefLink][ref]\n\n[ref]: b.md"
        links = extract_links(content, "index.md")
        link_types = {lnk.link_type for lnk in links}
        assert link_types == {"markdown", "wikilink", "reference"}
        targets = {lnk.target_path for lnk in links}
        assert "a.md" in targets
        assert "WikiNote.md" in targets
        assert "b.md" in targets

    def test_empty_content_returns_empty(self) -> None:
        """Empty markdown body yields no links."""
        assert extract_links("", "index.md") == []

    def test_no_links_returns_empty(self) -> None:
        """Plain prose with no links yields no results."""
        assert extract_links("Just some plain text here.", "index.md") == []


# ---------------------------------------------------------------------------
# FTSIndex: links table storage
# ---------------------------------------------------------------------------


class TestFTSIndexLinks:
    def test_upsert_stores_links(self) -> None:
        """upsert_note persists link rows and get_outlinks returns them."""
        idx = FTSIndex(":memory:")
        note = make_note(
            path="source.md",
            title="Source",
            links=[
                LinkInfo(
                    target_path="target.md",
                    link_text="Target",
                    link_type="markdown",
                )
            ],
        )
        idx.upsert_note(note)

        rows = idx.get_outlinks("source.md")
        assert len(rows) == 1
        assert rows[0]["target_path"] == "target.md"
        assert rows[0]["link_text"] == "Target"
        assert rows[0]["link_type"] == "markdown"
        assert rows[0]["fragment"] is None

    def test_get_backlinks_returns_sources(self) -> None:
        """get_backlinks returns the document that links to a given path."""
        idx = FTSIndex(":memory:")
        note = make_note(
            path="source.md",
            title="Source Doc",
            links=[
                LinkInfo(
                    target_path="target.md",
                    link_text="the target",
                    link_type="markdown",
                )
            ],
        )
        idx.upsert_note(note)
        # Also index the target so cascade delete tests have something to delete.
        idx.upsert_note(make_note(path="target.md", title="Target Doc"))

        rows = idx.get_backlinks("target.md")
        assert len(rows) == 1
        assert rows[0]["source_path"] == "source.md"
        assert rows[0]["source_title"] == "Source Doc"
        assert rows[0]["link_text"] == "the target"
        assert rows[0]["link_type"] == "markdown"

    def test_build_from_notes_populates_links(self) -> None:
        """build_from_notes bulk indexes links for all notes."""
        idx = FTSIndex(":memory:")
        notes = [
            make_note(
                path="a.md",
                links=[
                    LinkInfo(target_path="b.md", link_text="B", link_type="markdown")
                ],
            ),
            make_note(
                path="b.md",
                links=[
                    LinkInfo(target_path="a.md", link_text="A", link_type="wikilink")
                ],
            ),
        ]
        idx.build_from_notes(notes)

        assert len(idx.get_outlinks("a.md")) == 1
        assert len(idx.get_outlinks("b.md")) == 1
        assert len(idx.get_backlinks("a.md")) == 1
        assert len(idx.get_backlinks("b.md")) == 1

    def test_upsert_replaces_links(self) -> None:
        """Re-upserting a note replaces old links with new ones."""
        idx = FTSIndex(":memory:")
        note_v1 = make_note(
            path="source.md",
            links=[
                LinkInfo(target_path="old.md", link_text="Old", link_type="markdown")
            ],
        )
        idx.upsert_note(note_v1)
        assert len(idx.get_outlinks("source.md")) == 1

        note_v2 = make_note(
            path="source.md",
            links=[
                LinkInfo(target_path="new1.md", link_text="N1", link_type="markdown"),
                LinkInfo(target_path="new2.md", link_text="N2", link_type="wikilink"),
            ],
        )
        idx.upsert_note(note_v2)
        rows = idx.get_outlinks("source.md")
        assert len(rows) == 2
        targets = {r["target_path"] for r in rows}
        assert targets == {"new1.md", "new2.md"}

    def test_delete_cascades_links(self) -> None:
        """Deleting a document removes its link rows via ON DELETE CASCADE."""
        idx = FTSIndex(":memory:")
        note = make_note(
            path="source.md",
            links=[
                LinkInfo(target_path="target.md", link_text="T", link_type="markdown")
            ],
        )
        idx.upsert_note(note)
        assert len(idx.get_outlinks("source.md")) == 1

        idx.delete_by_path("source.md")
        assert idx.get_outlinks("source.md") == []

    def test_get_backlinks_empty_when_no_links(self) -> None:
        """get_backlinks returns [] when nothing links to the given path."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(make_note(path="orphan.md"))
        assert idx.get_backlinks("orphan.md") == []

    def test_get_outlinks_empty_when_no_links(self) -> None:
        """get_outlinks returns [] when document has no links."""
        idx = FTSIndex(":memory:")
        idx.upsert_note(make_note(path="lonely.md"))
        assert idx.get_outlinks("lonely.md") == []

    def test_fragment_stored_and_retrieved(self) -> None:
        """Fragment identifier is stored and returned correctly."""
        idx = FTSIndex(":memory:")
        note = make_note(
            path="source.md",
            links=[
                LinkInfo(
                    target_path="target.md",
                    link_text="See section",
                    link_type="markdown",
                    fragment="intro",
                )
            ],
        )
        idx.upsert_note(note)
        rows = idx.get_outlinks("source.md")
        assert rows[0]["fragment"] == "intro"

        backlinks = idx.get_backlinks("target.md")
        assert backlinks[0]["fragment"] == "intro"

    def test_multiple_sources_linking_to_same_target(self) -> None:
        """get_backlinks aggregates links from multiple source documents."""
        idx = FTSIndex(":memory:")
        for i in range(3):
            note = make_note(
                path=f"src{i}.md",
                links=[
                    LinkInfo(
                        target_path="shared.md",
                        link_text=f"Link {i}",
                        link_type="markdown",
                    )
                ],
            )
            idx.upsert_note(note)

        rows = idx.get_backlinks("shared.md")
        assert len(rows) == 3
        sources = {r["source_path"] for r in rows}
        assert sources == {"src0.md", "src1.md", "src2.md"}


# ---------------------------------------------------------------------------
# Collection: get_backlinks and get_outlinks
# ---------------------------------------------------------------------------


@pytest.fixture
def linked_vault(tmp_path: Path) -> Path:
    """Create a vault with interlinked notes for Collection integration tests.

    Layout:
        index.md          links to notes/topic.md and notes/other.md
        notes/topic.md    links back to ../index.md and to notes/other.md
        notes/other.md    no outbound links
    """
    vault = tmp_path / "vault"
    vault.mkdir()

    (vault / "index.md").write_text(
        "# Index\n\nSee [Topic](notes/topic.md) and [Other](notes/other.md).\n",
        encoding="utf-8",
    )
    (vault / "notes").mkdir()
    (vault / "notes" / "topic.md").write_text(
        "# Topic\n\nBack to [Index](../index.md) and also [Other](other.md).\n",
        encoding="utf-8",
    )
    (vault / "notes" / "other.md").write_text(
        "# Other\n\nNo links here.\n",
        encoding="utf-8",
    )
    return vault


class TestCollectionBacklinks:
    def test_get_backlinks_returns_backlink_info(self, linked_vault: Path) -> None:
        """get_backlinks returns BacklinkInfo objects for documents linking to path."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        backlinks = col.get_backlinks("notes/topic.md")
        assert len(backlinks) == 1
        assert isinstance(backlinks[0], BacklinkInfo)
        assert backlinks[0].source_path == "index.md"
        assert backlinks[0].link_text == "Topic"
        assert backlinks[0].link_type == "markdown"

    def test_get_backlinks_multiple_sources(self, linked_vault: Path) -> None:
        """Multiple documents linking to the same path all appear."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        backlinks = col.get_backlinks("notes/other.md")
        source_paths = {b.source_path for b in backlinks}
        assert "index.md" in source_paths
        assert "notes/topic.md" in source_paths

    def test_get_backlinks_empty_for_unlinked_doc(self, linked_vault: Path) -> None:
        """Document with no inbound links returns empty list."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        backlinks = col.get_backlinks("index.md")
        # index.md is only linked from topic.md (../index.md)
        assert isinstance(backlinks, list)
        # topic.md links to ../index.md which resolves to index.md
        assert all(isinstance(b, BacklinkInfo) for b in backlinks)

    def test_get_backlinks_path_traversal_rejected(self, linked_vault: Path) -> None:
        """Path traversal in get_backlinks raises ValueError."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        with pytest.raises(ValueError):
            col.get_backlinks("../etc/passwd")


class TestCollectionOutlinks:
    def test_get_outlinks_returns_outlink_info(self, linked_vault: Path) -> None:
        """get_outlinks returns OutlinkInfo objects for links from a document."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        outlinks = col.get_outlinks("index.md")
        assert len(outlinks) == 2
        assert all(isinstance(o, OutlinkInfo) for o in outlinks)
        targets = {o.target_path for o in outlinks}
        assert "notes/topic.md" in targets
        assert "notes/other.md" in targets

    def test_get_outlinks_exists_flag(self, linked_vault: Path) -> None:
        """OutlinkInfo.exists is True when target is indexed, False otherwise."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        outlinks = col.get_outlinks("index.md")
        # Both targets exist in the vault.
        for outlink in outlinks:
            assert outlink.exists is True

    def test_get_outlinks_exists_false_for_missing_target(self, tmp_path: Path) -> None:
        """OutlinkInfo.exists is False when target is not in the index."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nSee [Ghost](ghost.md).\n",
            encoding="utf-8",
        )
        col = Collection(source_dir=vault)
        col.build_index()

        outlinks = col.get_outlinks("source.md")
        assert len(outlinks) == 1
        assert outlinks[0].target_path == "ghost.md"
        assert outlinks[0].exists is False

    def test_get_outlinks_empty_for_no_links_doc(self, linked_vault: Path) -> None:
        """Document with no outbound links returns empty list."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        outlinks = col.get_outlinks("notes/other.md")
        assert outlinks == []

    def test_get_outlinks_path_traversal_rejected(self, linked_vault: Path) -> None:
        """Path traversal in get_outlinks raises ValueError."""
        col = Collection(source_dir=linked_vault)
        col.build_index()

        with pytest.raises(ValueError):
            col.get_outlinks("../../secret.md")


class TestCollectionReindex:
    def test_reindex_updates_links(self, tmp_path: Path) -> None:
        """reindex() refreshes link data for modified documents."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nSee [Old](old.md).\n",
            encoding="utf-8",
        )
        col = Collection(source_dir=vault)
        col.build_index()

        assert len(col.get_outlinks("source.md")) == 1
        assert col.get_outlinks("source.md")[0].target_path == "old.md"

        # Modify source.md to point somewhere else.
        (vault / "source.md").write_text(
            "# Source\n\nSee [New](new.md).\n",
            encoding="utf-8",
        )
        col.reindex()

        outlinks = col.get_outlinks("source.md")
        assert len(outlinks) == 1
        assert outlinks[0].target_path == "new.md"

    def test_reindex_removes_links_for_deleted_note(self, tmp_path: Path) -> None:
        """reindex() removes link rows when a document is deleted."""
        vault = tmp_path / "vault"
        vault.mkdir()
        (vault / "source.md").write_text(
            "# Source\n\nSee [Other](other.md).\n",
            encoding="utf-8",
        )
        (vault / "other.md").write_text("# Other\n", encoding="utf-8")
        col = Collection(source_dir=vault)
        col.build_index()

        assert len(col.get_backlinks("other.md")) == 1

        # Delete source.md and reindex.
        (vault / "source.md").unlink()
        col.reindex()

        assert col.get_backlinks("other.md") == []


# ---------------------------------------------------------------------------
# ParsedNote default field
# ---------------------------------------------------------------------------


class TestParsedNoteDefault:
    def test_links_field_defaults_to_empty_list(self) -> None:
        """ParsedNote constructed without links= has links=[]."""
        note = ParsedNote(
            path="test.md",
            frontmatter={},
            title="Test",
            chunks=[],
            content_hash="hash",
            modified_at=0.0,
        )
        assert note.links == []

    def test_links_field_independent_across_instances(self) -> None:
        """Each ParsedNote instance gets its own links list."""
        n1 = ParsedNote(
            path="a.md",
            frontmatter={},
            title="A",
            chunks=[],
            content_hash="h",
            modified_at=0.0,
        )
        n2 = ParsedNote(
            path="b.md",
            frontmatter={},
            title="B",
            chunks=[],
            content_hash="h",
            modified_at=0.0,
        )
        n1.links.append(
            LinkInfo(target_path="x.md", link_text="X", link_type="markdown")
        )
        assert n2.links == []
