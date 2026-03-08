"""Thin facade tying all markdown-vault-mcp modules together.

:class:`Collection` is the primary public API for the library.  MCP tools,
LangChain wrappers, and CLI commands all go through this class.
"""

from __future__ import annotations

import contextlib
import fnmatch
import json
import logging
import shutil
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import frontmatter as fm

from markdown_vault_mcp.exceptions import (
    DocumentExistsError,
    DocumentNotFoundError,
    EditConflictError,
    ReadOnlyError,
)
from markdown_vault_mcp.fts_index import FTSIndex, _derive_folder
from markdown_vault_mcp.scanner import (
    ChunkStrategy,
    HeadingChunker,
    WholeDocumentChunker,
    parse_note,
    scan_directory,
)
from markdown_vault_mcp.tracker import ChangeTracker
from markdown_vault_mcp.types import (
    CollectionStats,
    DeleteResult,
    EditResult,
    IndexStats,
    NoteContent,
    NoteInfo,
    ParsedNote,
    ReindexResult,
    RenameResult,
    SearchResult,
    WriteCallback,
    WriteResult,
)

if TYPE_CHECKING:
    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.vector_index import VectorIndex

logger = logging.getLogger(__name__)

_DEFAULT_STATE_SUBDIR = ".markdown_vault_mcp"
_DEFAULT_STATE_FILENAME = "state.json"

# RRF constant — standard value recommended in the original paper.
_RRF_K = 60


def _resolve_chunk_strategy(strategy: str | ChunkStrategy) -> ChunkStrategy:
    """Return a concrete ChunkStrategy from a string name or pass-through.

    Args:
        strategy: Either ``"heading"``, ``"whole"``, or a :class:`ChunkStrategy`
            instance.

    Returns:
        A concrete :class:`ChunkStrategy` instance.

    Raises:
        ValueError: If *strategy* is an unrecognised string name.
    """
    if isinstance(strategy, str):
        if strategy == "heading":
            return HeadingChunker()
        if strategy == "whole":
            return WholeDocumentChunker()
        raise ValueError(
            f"Unknown chunk_strategy {strategy!r}. "
            "Valid string values: 'heading', 'whole'."
        )
    return strategy


def _fts_row_to_note_info(row: dict) -> NoteInfo:
    """Convert an FTSIndex list_notes() row dict to a :class:`NoteInfo`.

    Args:
        row: Dict returned by :meth:`FTSIndex.list_notes` or
            :meth:`FTSIndex.get_note`.

    Returns:
        A populated :class:`NoteInfo` instance.
    """
    frontmatter: dict = {}
    raw_json = row.get("frontmatter_json")
    if raw_json:
        try:
            frontmatter = json.loads(raw_json)
        except (json.JSONDecodeError, TypeError):
            logger.warning(
                "Could not parse frontmatter_json for path %s", row.get("path")
            )
    return NoteInfo(
        path=row["path"],
        title=row["title"],
        folder=row["folder"],
        frontmatter=frontmatter,
        modified_at=row["modified_at"],
    )


class Collection:
    """Facade over FTS5 index, vector index, and change tracker.

    Instantiate once per collection root.  Call :meth:`build_index` (or let
    lazy initialisation handle it) before querying.

    Args:
        source_dir: Root directory of the markdown collection.
        index_path: Path to the SQLite index file.  ``None`` (default) uses
            an in-memory database that is discarded when the object is
            collected.
        embeddings_path: Base path for the ``{path}.npy`` and
            ``{path}.json`` sidecar files.  ``None`` (default) means
            semantic search is disabled.
        embedding_provider: Provider used to generate embeddings.  Required
            when *embeddings_path* is set.
        read_only: When ``True`` (default), write operations raise
            :exc:`~markdown_vault_mcp.exceptions.ReadOnlyError`.
        state_path: Path to the hash-state JSON file used by
            :class:`~markdown_vault_mcp.tracker.ChangeTracker`.  Defaults to
            ``{source_dir}/.markdown_vault_mcp/state.json``.
        indexed_frontmatter_fields: Frontmatter keys whose values are
            promoted to the ``document_tags`` table for structured filtering.
        required_frontmatter: If provided, documents missing any listed field
            are excluded from the index entirely.
        chunk_strategy: ``"heading"`` (default), ``"whole"``, or a custom
            :class:`~markdown_vault_mcp.scanner.ChunkStrategy` instance.
        on_write: Optional callback invoked after every successful write
            operation.  Signature:
            ``Callable[[Path, str, Literal["write","edit","delete","rename"]], None]``.
    """

    def __init__(
        self,
        *,
        source_dir: Path,
        index_path: Path | None = None,
        embeddings_path: Path | None = None,
        embedding_provider: EmbeddingProvider | None = None,
        read_only: bool = True,
        state_path: Path | None = None,
        indexed_frontmatter_fields: list[str] | None = None,
        required_frontmatter: list[str] | None = None,
        chunk_strategy: str | ChunkStrategy = "heading",
        on_write: WriteCallback | None = None,
        exclude_patterns: list[str] | None = None,
    ) -> None:
        self._source_dir = source_dir
        self._index_path = index_path
        self._embeddings_path = embeddings_path
        self._embedding_provider = embedding_provider
        self._read_only = read_only
        self._indexed_frontmatter_fields: list[str] = indexed_frontmatter_fields or []
        self._required_frontmatter = required_frontmatter
        self._chunk_strategy = _resolve_chunk_strategy(chunk_strategy)
        self._on_write = on_write
        self._exclude_patterns = exclude_patterns

        # Default state path: {source_dir}/.markdown_vault_mcp/state.json
        if state_path is None:
            self._state_path = (
                source_dir / _DEFAULT_STATE_SUBDIR / _DEFAULT_STATE_FILENAME
            )
        else:
            self._state_path = state_path

        # Sub-module construction.
        db_path: Path | str = index_path if index_path is not None else ":memory:"
        self._fts = FTSIndex(
            db_path=db_path,
            indexed_frontmatter_fields=self._indexed_frontmatter_fields or None,
        )
        self._tracker = ChangeTracker(self._state_path)

        # Vector index is loaded lazily (only if embeddings_path is set).
        self._vectors: VectorIndex | None = None

        # Lazy initialisation flag.
        self._initialized = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Release resources held by the collection (close the SQLite connection)."""
        self._fts.close()

    # ------------------------------------------------------------------
    # Lazy initialisation
    # ------------------------------------------------------------------

    def _ensure_initialized(self) -> None:
        """Build the FTS index on first access if it has not been built yet."""
        if not self._initialized:
            self.build_index()

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        mode: Literal["keyword", "semantic", "hybrid"] = "keyword",
        filters: dict[str, str] | None = None,
        folder: str | None = None,
    ) -> list[SearchResult]:
        """Search the collection.

        Args:
            query: Search string.
            limit: Maximum number of results to return.
            mode: ``"keyword"`` for BM25 FTS5, ``"semantic"`` for cosine
                similarity, or ``"hybrid"`` for Reciprocal Rank Fusion of both.
            filters: Dict of ``{frontmatter_key: value}`` pairs (AND semantics).
                Only works for fields in ``indexed_frontmatter_fields``.
            folder: If provided, restrict results to documents in this folder
                (and its sub-folders).

        Returns:
            List of :class:`~markdown_vault_mcp.types.SearchResult` ordered by
            relevance.

        Raises:
            ValueError: If *mode* is ``"semantic"`` or ``"hybrid"`` but no
                embedding provider or embeddings path is configured.
        """
        self._ensure_initialized()

        if mode == "keyword":
            return self._keyword_search(
                query, limit=limit, filters=filters, folder=folder
            )

        if mode == "semantic":
            self._require_vectors()
            return self._semantic_search(
                query, limit=limit, filters=filters, folder=folder
            )

        # hybrid
        self._require_vectors()
        return self._hybrid_search(query, limit=limit, filters=filters, folder=folder)

    def _require_vectors(self) -> None:
        """Raise ValueError if semantic search is not configured."""
        if self._embedding_provider is None or self._embeddings_path is None:
            raise ValueError(
                "Semantic search requires both 'embedding_provider' and "
                "'embeddings_path' to be configured."
            )

    def _load_vectors(self) -> VectorIndex:
        """Load or return the cached VectorIndex.

        Returns:
            A :class:`~markdown_vault_mcp.vector_index.VectorIndex` instance.
        """
        if self._vectors is not None:
            return self._vectors

        from markdown_vault_mcp.vector_index import VectorIndex

        assert self._embeddings_path is not None
        assert self._embedding_provider is not None

        npy_path = Path(str(self._embeddings_path) + ".npy")
        if npy_path.exists():
            self._vectors = VectorIndex.load(
                self._embeddings_path, self._embedding_provider
            )
            logger.info("Loaded vector index from %s", self._embeddings_path)
        else:
            self._vectors = VectorIndex(self._embedding_provider)
            logger.info("No vector index on disk; created empty VectorIndex")

        return self._vectors

    def _keyword_search(
        self,
        query: str,
        *,
        limit: int,
        filters: dict[str, str] | None,
        folder: str | None,
    ) -> list[SearchResult]:
        fts_results = self._fts.search(
            query, limit=limit, filters=filters, folder=folder
        )
        return [
            SearchResult(
                path=r.path,
                title=r.title,
                folder=r.folder,
                heading=r.heading,
                content=r.content,
                score=r.score,
                search_type="keyword",
                frontmatter=self._get_frontmatter(r.path),
            )
            for r in fts_results
        ]

    def _semantic_search(
        self,
        query: str,
        *,
        limit: int,
        filters: dict[str, str] | None = None,
        folder: str | None = None,
    ) -> list[SearchResult]:
        vectors = self._load_vectors()
        # Fetch extra candidates so post-filtering still yields *limit* results.
        candidate_limit = max(limit * 3, 30) if (folder or filters) else limit
        raw = vectors.search(query, limit=candidate_limit)

        results: list[SearchResult] = []
        for r in raw:
            if len(results) >= limit:
                break

            # Apply folder prefix filter.
            if folder is not None:
                r_folder = r.get("folder", "")
                if r_folder != folder and not r_folder.startswith(folder + "/"):
                    continue

            # Apply tag filters: check FTS index for each required tag.
            if filters:
                note_row = self._fts.get_note(r["path"])
                if note_row is None:
                    continue
                fm_raw = note_row.get("frontmatter_json")
                fm: dict = {}
                if fm_raw:
                    with contextlib.suppress(ValueError, TypeError):
                        fm = json.loads(fm_raw)
                match = True
                for key, value in filters.items():
                    fm_val = fm.get(key)
                    if fm_val is None:
                        match = False
                        break
                    # Support both scalar and list values.
                    if isinstance(fm_val, list):
                        if str(value) not in [str(v) for v in fm_val]:
                            match = False
                            break
                    else:
                        if str(fm_val) != str(value):
                            match = False
                            break
                if not match:
                    continue

            results.append(
                SearchResult(
                    path=r["path"],
                    title=r["title"],
                    folder=r["folder"],
                    heading=r.get("heading"),
                    content=r["content"],
                    score=r["score"],
                    search_type="semantic",
                    frontmatter=self._get_frontmatter(r["path"]),
                )
            )
        return results

    def _hybrid_search(
        self,
        query: str,
        *,
        limit: int,
        filters: dict[str, str] | None,
        folder: str | None,
    ) -> list[SearchResult]:
        """RRF merge of keyword and semantic results.

        Each result set is ranked independently.  Merged score:
        ``1 / (k + rank)`` where k=60.  Results appearing in both sets have
        their scores summed.  Returns top *limit* by total RRF score.
        """
        # Fetch more candidates than needed so RRF has enough to rank.
        candidate_limit = max(limit * 2, 20)

        fts_results = self._fts.search(
            query, limit=candidate_limit, filters=filters, folder=folder
        )
        vectors = self._load_vectors()
        vec_results = vectors.search(query, limit=candidate_limit)

        # Build a key for deduplication: (path, heading) identifies a chunk.
        # Use a dict to accumulate RRF scores and store metadata.
        rrf_scores: dict[tuple[str, str | None], float] = {}
        # Store the best metadata dict keyed by (path, heading).
        chunk_meta: dict[tuple[str, str | None], dict] = {}

        for rank, r in enumerate(fts_results, start=1):
            key = (r.path, r.heading)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
            if key not in chunk_meta:
                chunk_meta[key] = {
                    "path": r.path,
                    "title": r.title,
                    "folder": r.folder,
                    "heading": r.heading,
                    "content": r.content,
                    "search_type": "keyword",
                }

        for rank, r in enumerate(vec_results, start=1):
            # Apply folder prefix filter to semantic results.
            if folder is not None:
                r_folder = r.get("folder", "")
                if r_folder != folder and not r_folder.startswith(folder + "/"):
                    continue

            # Apply tag filters to semantic results via frontmatter lookup.
            if filters:
                note_row = self._fts.get_note(r["path"])
                if note_row is None:
                    continue
                fm_raw = note_row.get("frontmatter_json")
                fm: dict = {}
                if fm_raw:
                    with contextlib.suppress(json.JSONDecodeError, TypeError):
                        fm = json.loads(fm_raw)
                skip = False
                for key, value in filters.items():
                    fm_val = fm.get(key)
                    if fm_val is None:
                        skip = True
                        break
                    if isinstance(fm_val, list):
                        if str(value) not in [str(v) for v in fm_val]:
                            skip = True
                            break
                    else:
                        if str(fm_val) != str(value):
                            skip = True
                            break
                if skip:
                    continue

            heading = r.get("heading")
            key = (r["path"], heading)
            rrf_scores[key] = rrf_scores.get(key, 0.0) + 1.0 / (_RRF_K + rank)
            if key not in chunk_meta:
                chunk_meta[key] = {
                    "path": r["path"],
                    "title": r["title"],
                    "folder": r["folder"],
                    "heading": heading,
                    "content": r["content"],
                    "search_type": "semantic",
                }

        # Sort by descending RRF score, take top limit.
        sorted_keys = sorted(rrf_scores, key=lambda k: rrf_scores[k], reverse=True)[
            :limit
        ]

        return [
            SearchResult(
                path=chunk_meta[k]["path"],
                title=chunk_meta[k]["title"],
                folder=chunk_meta[k]["folder"],
                heading=chunk_meta[k]["heading"],
                content=chunk_meta[k]["content"],
                score=rrf_scores[k],
                search_type=chunk_meta[k]["search_type"],
                frontmatter=self._get_frontmatter(chunk_meta[k]["path"]),
            )
            for k in sorted_keys
        ]

    def _get_frontmatter(self, path: str) -> dict:
        """Return the frontmatter dict for a document from the FTS index.

        Falls back to an empty dict if the document is not found.

        Args:
            path: Relative document path.

        Returns:
            Parsed frontmatter dict.
        """
        row = self._fts.get_note(path)
        if row is None:
            return {}
        raw = row.get("frontmatter_json")
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return {}

    # ------------------------------------------------------------------
    # Read / list
    # ------------------------------------------------------------------

    def read(self, path: str) -> NoteContent | None:
        """Read the full content of a document from disk.

        Args:
            path: Relative document path (e.g. ``"Journal/note.md"``).

        Returns:
            A :class:`~markdown_vault_mcp.types.NoteContent` instance, or ``None``
            if the file does not exist.
        """
        self._ensure_initialized()

        abs_path = (self._source_dir / path).resolve()
        if not abs_path.is_relative_to(self._source_dir.resolve()):
            return None
        if not abs_path.is_file():
            return None

        try:
            note = parse_note(abs_path, self._source_dir, self._chunk_strategy)
        except (UnicodeDecodeError, OSError) as exc:
            logger.warning("read(%s): could not parse file — %s", path, exc)
            return None

        raw_content = abs_path.read_text(encoding="utf-8")
        folder = str(Path(path).parent)
        if folder == ".":
            folder = ""

        return NoteContent(
            path=note.path,
            title=note.title,
            folder=folder,
            content=raw_content,
            frontmatter=note.frontmatter,
            modified_at=note.modified_at,
        )

    def list(
        self,
        *,
        folder: str | None = None,
        pattern: str | None = None,
    ) -> list[NoteInfo]:
        """List documents in the collection.

        Args:
            folder: If provided, only return documents in this folder (and
                sub-folders).
            pattern: Unix glob matched against the relative path using
                :func:`fnmatch.fnmatch`.  Example: ``"Journal/*.md"``.

        Returns:
            List of :class:`~markdown_vault_mcp.types.NoteInfo` objects.
        """
        self._ensure_initialized()

        rows = self._fts.list_notes(folder=folder)
        notes = [_fts_row_to_note_info(row) for row in rows]

        if pattern:
            notes = [n for n in notes if fnmatch.fnmatch(n.path, pattern)]

        return notes

    # ------------------------------------------------------------------
    # Index management
    # ------------------------------------------------------------------

    def build_index(self, *, force: bool = False) -> IndexStats:
        """Scan source_dir and build the FTS index.

        If the index already contains documents and *force* is ``False``,
        this is a no-op.  ``force=True`` drops all existing data and rebuilds
        from scratch.

        Args:
            force: When ``True``, drop and rebuild the index unconditionally.

        Returns:
            :class:`~markdown_vault_mcp.types.IndexStats` describing what was indexed.
        """
        # Check if index already has data and we are not forcing.
        if not force and self._initialized:
            existing = self._fts.list_notes()
            if existing:
                logger.debug(
                    "build_index: index already populated (%d docs), skipping",
                    len(existing),
                )
                return IndexStats(
                    documents_indexed=len(existing),
                    chunks_indexed=0,
                    skipped=0,
                )

        if force:
            # Drop all data by rebuilding from an empty scan then re-populate.
            logger.info("build_index(force=True): dropping and rebuilding index")
            # Delete all existing documents.
            for row in self._fts.list_notes():
                self._fts.delete_by_path(row["path"])

        logger.info("build_index: scanning %s", self._source_dir)

        notes = list(
            scan_directory(
                self._source_dir,
                required_frontmatter=self._required_frontmatter,
                chunk_strategy=self._chunk_strategy,
                exclude_patterns=self._exclude_patterns,
            )
        )

        total_chunks = 0
        for note in notes:
            total_chunks += self._fts.upsert_note(note)

        # Count how many files were skipped due to required_frontmatter.
        # scan_directory logs skipped counts itself; we compute it by comparing
        # indexed count to total files on disk.
        all_files = list(self._source_dir.glob("**/*.md"))
        skipped = len(all_files) - len(notes)

        # Update tracker state so reindex() knows the baseline.
        self._tracker.update_state(notes)

        self._initialized = True
        logger.info(
            "build_index: indexed %d documents, %d chunks (%d skipped)",
            len(notes),
            total_chunks,
            skipped,
        )
        return IndexStats(
            documents_indexed=len(notes),
            chunks_indexed=total_chunks,
            skipped=max(skipped, 0),
        )

    def reindex(self) -> ReindexResult:
        """Incrementally update the index based on file changes.

        Uses :class:`~markdown_vault_mcp.tracker.ChangeTracker` to detect which
        files have been added, modified, or deleted since the last scan.
        Only changed files are re-parsed and re-indexed.

        Returns:
            :class:`~markdown_vault_mcp.types.ReindexResult` with counts of changes
            applied.
        """
        self._ensure_initialized()

        changes = self._tracker.detect_changes(self._source_dir)
        logger.info(
            "reindex: %d added, %d modified, %d deleted, %d unchanged",
            len(changes.added),
            len(changes.modified),
            len(changes.deleted),
            changes.unchanged,
        )

        # Delete removed documents.
        for path in changes.deleted:
            self._fts.delete_by_path(path)
            if self._vectors is not None:
                self._vectors.delete_by_path(path)

        # Parse and upsert added/modified documents.
        # Track actually-indexed counts separately from detected-change counts
        # so that skipped files (parse errors, missing required frontmatter)
        # are not reported as successfully added or modified.
        indexed_added = 0
        indexed_modified = 0
        added_set = set(changes.added)

        for path in changes.added + changes.modified:
            abs_path = self._source_dir / path
            try:
                note = parse_note(abs_path, self._source_dir, self._chunk_strategy)
            except (UnicodeDecodeError, OSError) as exc:
                logger.warning("reindex: skipping %s — %s", path, exc)
                continue
            except Exception as exc:
                logger.warning(
                    "reindex: skipping %s — parse error (%s)",
                    path,
                    exc,
                    exc_info=True,
                )
                continue

            # Apply required_frontmatter filter.
            if self._required_frontmatter:
                missing = [
                    f for f in self._required_frontmatter if f not in note.frontmatter
                ]
                if missing:
                    logger.info(
                        "reindex: skipping %s — missing frontmatter: %s", path, missing
                    )
                    continue

            self._fts.upsert_note(note)
            if path in added_set:
                indexed_added += 1
            else:
                indexed_modified += 1

            # Update vector index for changed notes if loaded.
            if self._vectors is not None and self._embeddings_path is not None:
                self._vectors.delete_by_path(note.path)
                texts = [c.content for c in note.chunks]
                meta = [
                    {
                        "path": note.path,
                        "title": note.title,
                        "folder": _derive_folder(note.path),
                        "heading": c.heading,
                        "content": c.content,
                    }
                    for c in note.chunks
                ]
                if texts:
                    self._vectors.add(texts, meta)

        # Persist updated vector index.
        if self._vectors is not None and self._embeddings_path is not None:
            self._vectors.save(self._embeddings_path)

        # Update tracker state: rebuild from current FTS index contents.
        # ChangeTracker.update_state needs objects with .path and .content_hash.
        state_notes: list[ParsedNote] = [
            ParsedNote(
                path=r["path"],
                frontmatter={},
                title=r["title"],
                chunks=[],
                content_hash=r["content_hash"],
                modified_at=r["modified_at"],
            )
            for r in self._fts.list_notes()
        ]
        self._tracker.update_state(state_notes)

        return ReindexResult(
            added=indexed_added,
            modified=indexed_modified,
            deleted=len(changes.deleted),
            unchanged=changes.unchanged,
        )

    def build_embeddings(self, *, force: bool = False) -> int:
        """Build the vector index from all chunks currently in the FTS index.

        Args:
            force: If ``True``, rebuild from scratch even if a vector index
                already exists on disk.

        Returns:
            Total number of chunks embedded.

        Raises:
            ValueError: If ``embedding_provider`` or ``embeddings_path`` is
                not configured.
        """
        self._ensure_initialized()
        self._require_vectors()

        assert self._embeddings_path is not None
        assert self._embedding_provider is not None

        from markdown_vault_mcp.vector_index import VectorIndex

        if force or self._vectors is None:
            self._vectors = VectorIndex(self._embedding_provider)
        elif not force:
            # If a persisted index already exists and we are not forcing,
            # return the existing count without rebuilding.
            npy_path = Path(str(self._embeddings_path) + ".npy")
            if npy_path.exists() and self._vectors.count > 0:
                logger.info(
                    "build_embeddings: index already exists (%d chunks), skipping",
                    self._vectors.count,
                )
                return self._vectors.count

        rows = self._fts.list_notes()
        texts: list[str] = []
        meta: list[dict] = []

        for row in rows:
            path = row["path"]
            title = row["title"]
            folder = row["folder"]
            # Re-parse to get chunks with content.
            abs_path = self._source_dir / path
            try:
                note = parse_note(abs_path, self._source_dir, self._chunk_strategy)
            except (UnicodeDecodeError, OSError) as exc:
                logger.warning("build_embeddings: skipping %s — %s", path, exc)
                continue
            for chunk in note.chunks:
                texts.append(chunk.content)
                meta.append(
                    {
                        "path": path,
                        "title": title,
                        "folder": folder,
                        "heading": chunk.heading,
                        "content": chunk.content,
                    }
                )

        if texts:
            self._vectors.add(texts, meta)

        self._vectors.save(self._embeddings_path)
        logger.info("build_embeddings: embedded and saved %d chunks", len(texts))
        return len(texts)

    def embeddings_status(self) -> dict:
        """Return status information about the vector index.

        Returns:
            Dict with keys ``provider``, ``chunk_count``, ``path``,
            ``available``.
        """
        if self._embedding_provider is None or self._embeddings_path is None:
            return {
                "available": False,
                "provider": None,
                "chunk_count": 0,
                "path": None,
            }

        count = 0
        if self._vectors is not None:
            count = self._vectors.count
        else:
            npy_path = Path(str(self._embeddings_path) + ".npy")
            if npy_path.exists():
                # Peek at metadata file for count without loading full matrix.
                json_path = Path(str(self._embeddings_path) + ".json")
                if json_path.exists():
                    try:
                        with json_path.open(encoding="utf-8") as fh:
                            loaded_meta = json.load(fh)
                        count = len(loaded_meta)
                    except (OSError, json.JSONDecodeError):
                        pass

        return {
            "available": True,
            "provider": type(self._embedding_provider).__name__,
            "chunk_count": count,
            "path": str(self._embeddings_path),
        }

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    def list_folders(self) -> list[str]:
        """Return all distinct folder values across the indexed collection.

        Returns:
            Sorted list of folder strings (``""`` for the collection root).
        """
        self._ensure_initialized()
        return self._fts.list_folders()

    def list_tags(self, field: str = "tags") -> list[str]:
        """Return all distinct values indexed for a given frontmatter field.

        If *field* was not in ``indexed_frontmatter_fields``, returns ``[]``.

        Args:
            field: Frontmatter key to query (default: ``"tags"``).

        Returns:
            Sorted list of distinct value strings.
        """
        self._ensure_initialized()
        return self._fts.list_field_values(field)

    def stats(self) -> CollectionStats:
        """Return collection-wide statistics.

        Returns:
            :class:`~markdown_vault_mcp.types.CollectionStats` snapshot.
        """
        self._ensure_initialized()

        rows = self._fts.list_notes()
        doc_count = len(rows)

        # Chunk count via the public FTSIndex method.
        chunk_count = self._fts.count_chunks()

        folders = self._fts.list_folders()
        folder_count = len(folders)

        semantic_available = (
            self._embedding_provider is not None and self._embeddings_path is not None
        )

        return CollectionStats(
            document_count=doc_count,
            chunk_count=chunk_count,
            folder_count=folder_count,
            semantic_search_available=semantic_available,
            indexed_frontmatter_fields=list(self._indexed_frontmatter_fields),
        )

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def _check_writable(self) -> None:
        """Raise ReadOnlyError if the collection is configured as read-only.

        Raises:
            ReadOnlyError: If ``read_only=True``.
        """
        if self._read_only:
            raise ReadOnlyError(
                "Collection is read-only; write operations are not permitted."
            )

    def _validate_path(self, path: str) -> Path:
        """Resolve a relative path and validate it is inside source_dir.

        Args:
            path: Relative document path.

        Returns:
            The resolved absolute path.

        Raises:
            ValueError: If the path escapes the source directory or does
                not end with ``.md``.
        """
        if not path.endswith(".md"):
            raise ValueError(f"Path must end with '.md': {path}")
        abs_path = (self._source_dir / path).resolve()
        if not abs_path.is_relative_to(self._source_dir.resolve()):
            raise ValueError(f"Path traversal detected: {path}")
        return abs_path

    def _update_vector_index(self, note: ParsedNote) -> None:
        """Update the vector index for a single document.

        Deletes existing entries for the document path and re-adds
        chunks if the vector index is active.

        Args:
            note: Parsed document to index.
        """
        if self._vectors is None or self._embeddings_path is None:
            return
        self._vectors.delete_by_path(note.path)
        texts = [c.content for c in note.chunks]
        meta = [
            {
                "path": note.path,
                "title": note.title,
                "folder": _derive_folder(note.path),
                "heading": c.heading,
                "content": c.content,
            }
            for c in note.chunks
        ]
        if texts:
            self._vectors.add(texts, meta)
        self._vectors.save(self._embeddings_path)

    def write(
        self,
        path: str,
        content: str,
        frontmatter: dict | None = None,
    ) -> WriteResult:
        """Create or overwrite a document.

        Creates intermediate directories as needed.  If *frontmatter* is
        provided, it is serialised as a YAML header at the top of the file.

        Args:
            path: Relative document path.
            content: Markdown body (excluding frontmatter).
            frontmatter: Optional frontmatter dict serialised as YAML header.

        Returns:
            :class:`~markdown_vault_mcp.types.WriteResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            ValueError: If *path* escapes the source directory.
        """
        self._check_writable()
        self._ensure_initialized()

        abs_path = self._validate_path(path)
        created = not abs_path.is_file()

        # Create intermediate directories.
        abs_path.parent.mkdir(parents=True, exist_ok=True)

        # Build file content with optional frontmatter.
        if frontmatter is not None:
            post = fm.Post(content, **frontmatter)
            file_content = fm.dumps(post)
        else:
            file_content = content

        abs_path.write_text(file_content, encoding="utf-8")

        # Update FTS index.
        note = parse_note(abs_path, self._source_dir, self._chunk_strategy)
        self._fts.upsert_note(note)

        # Update vector index if active.
        self._update_vector_index(note)

        # Trigger callback.
        if self._on_write is not None:
            self._on_write(abs_path, file_content, "write")

        return WriteResult(path=path, created=created)

    def edit(self, path: str, old_text: str, new_text: str) -> EditResult:
        """Patch a section of a document.

        Reads the file, verifies *old_text* exists exactly once in the
        full file content (including frontmatter), replaces it with
        *new_text*, and writes back.

        Args:
            path: Relative document path.
            old_text: Text to replace (must appear exactly once).
            new_text: Replacement text.

        Returns:
            :class:`~markdown_vault_mcp.types.EditResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            DocumentNotFoundError: If the file does not exist.
            EditConflictError: If *old_text* is not found or appears
                more than once.
        """
        self._check_writable()
        self._ensure_initialized()

        if not old_text:
            raise ValueError("old_text must not be empty")

        abs_path = self._validate_path(path)
        if not abs_path.is_file():
            raise DocumentNotFoundError(f"Document not found: {path}")

        file_content = abs_path.read_text(encoding="utf-8")
        count = file_content.count(old_text)

        if count == 0:
            raise EditConflictError(f"old_text not found in {path}")
        if count > 1:
            raise EditConflictError(
                f"old_text appears {count} times in {path}; must appear exactly once"
            )

        new_content = file_content.replace(old_text, new_text, 1)
        abs_path.write_text(new_content, encoding="utf-8")

        # Update FTS index.
        note = parse_note(abs_path, self._source_dir, self._chunk_strategy)
        self._fts.upsert_note(note)

        # Update vector index if active.
        self._update_vector_index(note)

        # Trigger callback.
        if self._on_write is not None:
            self._on_write(abs_path, new_content, "edit")

        return EditResult(path=path, replacements=1)

    def delete(self, path: str) -> DeleteResult:
        """Delete a document.

        Removes the file from disk and deletes all FTS and embedding
        index entries.

        Args:
            path: Relative document path.

        Returns:
            :class:`~markdown_vault_mcp.types.DeleteResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            DocumentNotFoundError: If the file does not exist.
        """
        self._check_writable()
        self._ensure_initialized()

        abs_path = self._validate_path(path)
        if not abs_path.is_file():
            raise DocumentNotFoundError(f"Document not found: {path}")

        # Remove file from disk.
        abs_path.unlink()

        # Delete FTS index entries.
        self._fts.delete_by_path(path)

        # Delete vector index entries if active.
        if self._vectors is not None and self._embeddings_path is not None:
            self._vectors.delete_by_path(path)
            self._vectors.save(self._embeddings_path)

        # Trigger callback.
        if self._on_write is not None:
            self._on_write(abs_path, "", "delete")

        return DeleteResult(path=path)

    def rename(self, old_path: str, new_path: str) -> RenameResult:
        """Rename or move a document.

        Renames the file on disk, deletes old index entries, and inserts
        new entries under the new path.  Creates intermediate directories
        for *new_path* as needed.

        Args:
            old_path: Current relative document path.
            new_path: Target relative document path.

        Returns:
            :class:`~markdown_vault_mcp.types.RenameResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            DocumentNotFoundError: If *old_path* does not exist.
            DocumentExistsError: If *new_path* already exists.
            ValueError: If either path escapes the source directory.
        """
        self._check_writable()
        self._ensure_initialized()

        old_abs = self._validate_path(old_path)
        new_abs = self._validate_path(new_path)

        if not old_abs.is_file():
            raise DocumentNotFoundError(f"Document not found: {old_path}")
        if new_abs.is_file():
            raise DocumentExistsError(f"Target already exists: {new_path}")

        # Create intermediate directories for new path.
        new_abs.parent.mkdir(parents=True, exist_ok=True)

        # Move file on disk.  shutil.move() handles cross-device renames
        # (copy+delete fallback) unlike Path.rename().
        shutil.move(str(old_abs), str(new_abs))

        # Delete old index entries.
        self._fts.delete_by_path(old_path)
        if self._vectors is not None:
            self._vectors.delete_by_path(old_path)

        # Insert new entries under the new path.
        note = parse_note(new_abs, self._source_dir, self._chunk_strategy)
        self._fts.upsert_note(note)

        # Update vector index if active.
        self._update_vector_index(note)

        # Read content for callback.
        new_content = new_abs.read_text(encoding="utf-8")

        # Trigger callback.
        if self._on_write is not None:
            self._on_write(new_abs, new_content, "rename")

        return RenameResult(old_path=old_path, new_path=new_path)
