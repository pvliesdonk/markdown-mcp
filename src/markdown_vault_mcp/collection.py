"""Thin facade tying all markdown-vault-mcp modules together.

:class:`Collection` is the primary public API for the library.  MCP tools,
LangChain wrappers, and CLI commands all go through this class.
"""

from __future__ import annotations

import base64
import contextlib
import fnmatch
import json
import logging
import mimetypes
import shutil
import threading
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

import frontmatter as fm

from markdown_vault_mcp.exceptions import (
    ConcurrentModificationError,
    DocumentExistsError,
    DocumentNotFoundError,
    EditConflictError,
    ReadOnlyError,
)
from markdown_vault_mcp.fts_index import FTSIndex, _derive_folder
from markdown_vault_mcp.hashing import compute_etag, compute_file_hash
from markdown_vault_mcp.scanner import (
    ChunkStrategy,
    HeadingChunker,
    WholeDocumentChunker,
    parse_note,
    scan_directory,
)
from markdown_vault_mcp.tracker import ChangeTracker
from markdown_vault_mcp.types import (
    AttachmentContent,
    AttachmentInfo,
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
    from collections.abc import Iterator

    from markdown_vault_mcp.git import GitWriteStrategy
    from markdown_vault_mcp.providers import EmbeddingProvider
    from markdown_vault_mcp.vector_index import VectorIndex

logger = logging.getLogger(__name__)

_DEFAULT_STATE_SUBDIR = ".markdown_vault_mcp"
_DEFAULT_STATE_FILENAME = "state.json"

# RRF constant — standard value recommended in the original paper.
_RRF_K = 60

# Default set of allowed attachment extensions (without leading dot, lower-case).
# .md is always excluded — it is always handled as a markdown note.
_DEFAULT_ATTACHMENT_EXTENSIONS: frozenset[str] = frozenset(
    [
        # Documents
        "pdf",
        "docx",
        "xlsx",
        "pptx",
        "odt",
        "ods",
        "odp",
        # Images
        "png",
        "jpg",
        "jpeg",
        "gif",
        "webp",
        "svg",
        "bmp",
        "tiff",
        # Archives
        "zip",
        "tar",
        "gz",
        # Audio / Video
        "mp3",
        "mp4",
        "wav",
        "ogg",
        # Text and data
        "txt",
        "csv",
        "tsv",
        "json",
        "yaml",
        "toml",
        "xml",
        "html",
        "css",
        "js",
        "ts",
    ]
)


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
        git_strategy: Optional git strategy used for background git tasks (e.g.
            periodic fetch + ff-only updates). Started via :meth:`start`.
        git_pull_interval_s: Interval in seconds for periodic pulls. ``0``
            disables the pull loop.
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
        git_strategy: GitWriteStrategy | None = None,
        git_pull_interval_s: int = 0,
        exclude_patterns: list[str] | None = None,
        attachment_extensions: list[str] | None = None,
        max_attachment_size_mb: float = 10.0,
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
        self._git_strategy = git_strategy
        self._git_pull_interval_s = git_pull_interval_s
        self._exclude_patterns = exclude_patterns
        self._attachment_extensions = attachment_extensions
        self._max_attachment_size_mb = max_attachment_size_mb

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

        # Serialise concurrent write operations on this instance.
        # Re-entrant: periodic pull tick blocks writes, then reindex() acquires
        # this lock again for its mutation phase.
        self._write_lock = threading.RLock()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    @contextlib.contextmanager
    def pause_writes(self) -> Iterator[None]:
        """Block all write operations until the context exits.

        Write operations are queued (blocked on the lock) rather than being
        rejected. Reads and search remain unblocked at the Python level.
        """
        with self._write_lock:
            yield

    def sync_from_remote_before_index(self) -> None:
        """One-time git fetch + ff-only update before build_index().

        Intended to run during server startup before the initial index build.
        No reindex is triggered here because build_index() will scan the updated
        working tree.
        """
        if self._git_strategy is None or self._git_pull_interval_s <= 0:
            return
        self._git_strategy.sync_once(self._source_dir)

    def start(self) -> None:
        """Start background tasks for this Collection (e.g. git pull loop)."""
        if self._git_strategy is None or self._git_pull_interval_s <= 0:
            return
        self._git_strategy.start(
            repo_path=self._source_dir,
            pull_interval_s=self._git_pull_interval_s,
            pause_writes=self.pause_writes,
            on_pull=self.reindex,
        )

    def stop(self) -> None:
        """Stop background tasks (e.g. git pull loop) without closing the collection.

        Safe to call multiple times.  A no-op if no pull loop was started.
        The SQLite connection and write callback remain open; only the pull
        loop thread is signalled to stop.
        """
        if self._git_strategy is not None:
            self._git_strategy.stop()

    def close(self) -> None:
        """Release resources held by the collection.

        Closes the SQLite connection and, if the ``on_write`` callback
        has a ``close()`` method (e.g. :class:`~markdown_vault_mcp.git.GitWriteStrategy`),
        calls it to flush pending git operations.
        """
        if self._git_strategy is not None:
            self._git_strategy.close()
        if (
            self._on_write is not None
            and self._on_write is not self._git_strategy
            and hasattr(self._on_write, "close")
        ):
            self._on_write.close()  # type: ignore[union-attr]
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
        etag = (
            note.content_hash
        )  # already computed by parse_note (SHA-256 of raw bytes)
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
            etag=etag,
        )

    def list(
        self,
        *,
        folder: str | None = None,
        pattern: str | None = None,
        include_attachments: bool = False,
    ) -> list[NoteInfo | AttachmentInfo]:
        """List documents (and optionally attachments) in the collection.

        Args:
            folder: If provided, only return documents in this folder (and
                sub-folders).
            pattern: Unix glob matched against the relative path using
                :func:`fnmatch.fnmatch`.  Example: ``"Journal/*.md"``.
            include_attachments: When ``True``, also return non-.md files
                that match the attachment allowlist.  Each
                :class:`~markdown_vault_mcp.types.AttachmentInfo` entry
                includes ``kind="attachment"`` and ``mime_type``.

        Returns:
            List of :class:`~markdown_vault_mcp.types.NoteInfo` (and
            optionally :class:`~markdown_vault_mcp.types.AttachmentInfo`)
            objects.
        """
        self._ensure_initialized()

        rows = self._fts.list_notes(folder=folder)
        notes: list[NoteInfo | AttachmentInfo] = [
            _fts_row_to_note_info(row) for row in rows
        ]

        if pattern:
            notes = [n for n in notes if fnmatch.fnmatch(n.path, pattern)]

        if not include_attachments:
            return notes

        exts = self._effective_attachment_extensions()
        source_resolved = self._source_dir.resolve()
        attachments: list[AttachmentInfo] = []

        # Attachment scan runs outside _write_lock — result is a best-effort
        # snapshot and is not atomic with the FTS note listing above.
        for abs_path in self._source_dir.rglob("*"):
            if not abs_path.is_file():
                continue
            if abs_path.suffix.lower() == ".md":
                continue
            suffix = abs_path.suffix.lstrip(".").lower()
            if "*" not in exts and suffix not in exts:
                continue
            try:
                rel = abs_path.relative_to(source_resolved)
            except ValueError:
                continue
            rel_path = str(rel)
            # Skip files where any path component (including the filename itself) starts with ".".
            if any(part.startswith(".") for part in rel.parts):
                continue
            # Apply exclude_patterns — mirrors scan_directory behaviour.
            rel_posix = rel.as_posix()
            if self._exclude_patterns and any(
                fnmatch.fnmatch(rel_posix, pat) for pat in self._exclude_patterns
            ):
                continue
            if pattern and not fnmatch.fnmatch(rel_path, pattern):
                continue
            rel_folder = str(Path(rel_path).parent)
            if rel_folder == ".":
                rel_folder = ""
            if (
                folder is not None
                and rel_folder != folder
                and not rel_folder.startswith(folder + "/")
            ):
                continue
            try:
                stat = abs_path.stat()
            except OSError:
                continue
            mime_type, _ = mimetypes.guess_type(rel_path)
            attachments.append(
                AttachmentInfo(
                    path=rel_path,
                    folder=rel_folder,
                    mime_type=mime_type,
                    size_bytes=stat.st_size,
                    modified_at=stat.st_mtime,
                )
            )

        return notes + attachments

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
        errored = 0
        for note in notes:
            try:
                total_chunks += self._fts.upsert_note(note)
            except Exception:
                errored += 1
                logger.warning(
                    "build_index: failed to index %s", note.path, exc_info=True
                )

        # Count how many files were skipped due to required_frontmatter.
        # scan_directory logs skipped counts itself; we compute it by comparing
        # indexed count to total files on disk.
        all_files = list(self._source_dir.glob("**/*.md"))
        skipped = len(all_files) - len(notes)

        # Update tracker state so reindex() knows the baseline.
        self._tracker.update_state(notes)

        self._initialized = True
        if errored:
            logger.warning(
                "build_index: indexed %d documents, %d chunks (%d skipped, %d errors)",
                len(notes) - errored,
                total_chunks,
                skipped,
                errored,
            )
        else:
            logger.info(
                "build_index: indexed %d documents, %d chunks (%d skipped)",
                len(notes),
                total_chunks,
                skipped,
            )
        return IndexStats(
            documents_indexed=len(notes) - errored,
            chunks_indexed=total_chunks,
            skipped=max(skipped, 0),
        )

    def reindex(self) -> ReindexResult:
        """Incrementally update the index based on file changes.

        Uses :class:`~markdown_vault_mcp.tracker.ChangeTracker` to detect which
        files have been added, modified, or deleted since the last scan.
        Only changed files are re-parsed and re-indexed.

        Thread-safety: the filesystem scan runs without holding ``_write_lock``
        (read-only), then the mutation phase acquires the lock to prevent races
        with concurrent write/edit/delete/rename operations.

        Returns:
            :class:`~markdown_vault_mcp.types.ReindexResult` with counts of changes
            applied.
        """
        self._ensure_initialized()

        # Phase 1: scan (outside lock — read-only filesystem walk + hashing).
        changes = self._tracker.detect_changes(self._source_dir)
        logger.info(
            "reindex: %d added, %d modified, %d deleted, %d unchanged",
            len(changes.added),
            len(changes.modified),
            len(changes.deleted),
            changes.unchanged,
        )

        # Pre-parse notes outside the lock to minimise lock hold time.
        # NOTE: there is an inherent TOCTOU window between detecting a change
        # in Phase 1 (hash comparison) and re-reading the file for indexing
        # here.  If the file is modified again in that window, the newly
        # written content is indexed rather than the version that triggered
        # the change.  This is acceptable — the next reindex() call will
        # reconcile the difference.
        parsed: list[tuple[str, ParsedNote]] = []
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

            parsed.append((path, note))

        # Phase 2: apply mutations (inside lock — prevents races with writes).
        with self._write_lock:
            # Delete removed documents.
            for path in changes.deleted:
                self._fts.delete_by_path(path)
                if self._vectors is not None:
                    self._vectors.delete_by_path(path)

            # Upsert parsed notes.
            indexed_added = 0
            indexed_modified = 0
            added_set = set(changes.added)

            for path, note in parsed:
                try:
                    self._fts.upsert_note(note)
                except Exception:
                    logger.warning("reindex: failed to index %s", path, exc_info=True)
                    continue
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

    def get_toc(self, path: str) -> list[dict[str, Any]]:
        """Return table of contents for a document.

        Queries the FTS sections table for headings and prepends the document
        title as a synthetic H1 entry.

        Args:
            path: Relative path to the document (e.g. ``"notes/intro.md"``).

        Returns:
            List of ``{"heading": str, "level": int}`` dicts ordered by
            position, with the document title prepended as level 1.

        Raises:
            ValueError: If no document exists at the given path.
        """
        self._ensure_initialized()
        self._validate_path(path)

        row = self._fts.get_note(path)
        if row is None:
            raise ValueError(f"Document not found: {path}")

        title: str = row["title"]
        headings = self._fts.get_toc(path)

        # Prepend a synthetic H1 for the document title, filtering out any
        # real H1 that duplicates it (common when docs start with ``# Title``).
        toc: list[dict[str, Any]] = [{"heading": title, "level": 1}]
        toc.extend(
            h for h in headings if not (h["level"] == 1 and h["heading"] == title)
        )
        return toc

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

        exts = self._effective_attachment_extensions()
        attachment_extensions = ["*"] if "*" in exts else sorted(exts)

        return CollectionStats(
            document_count=doc_count,
            chunk_count=chunk_count,
            folder_count=folder_count,
            semantic_search_available=semantic_available,
            indexed_frontmatter_fields=list(self._indexed_frontmatter_fields),
            attachment_extensions=attachment_extensions,
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

    def _effective_attachment_extensions(self) -> frozenset[str]:
        """Return the effective set of allowed attachment extensions.

        Returns:
            Frozenset of lower-case extension strings (without leading dot).
            The special value ``frozenset(["*"])`` means all non-.md files.
        """
        if self._attachment_extensions is None:
            return _DEFAULT_ATTACHMENT_EXTENSIONS
        return frozenset(self._attachment_extensions)

    def _is_attachment(self, path: str) -> bool:
        """Return True if *path* is an allowed non-.md attachment.

        Args:
            path: Relative path to check.

        Returns:
            ``True`` when the extension is in the allowlist and is not ``.md``.
        """
        if path.endswith(".md"):
            return False
        suffix = Path(path).suffix.lstrip(".").lower()
        exts = self._effective_attachment_extensions()
        return "*" in exts or suffix in exts

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

    def _validate_attachment_path(self, path: str) -> Path:
        """Resolve and validate a non-.md attachment path.

        Args:
            path: Relative attachment path.

        Returns:
            The resolved absolute path.

        Raises:
            ValueError: If the path escapes the source directory, ends with
                ``.md``, or has an extension not in the attachment allowlist.
        """
        if path.endswith(".md"):
            raise ValueError(
                f"Path ends with '.md' — use the note read/write methods instead: {path}"
            )
        exts = self._effective_attachment_extensions()
        suffix = Path(path).suffix.lstrip(".").lower()
        if "*" not in exts and suffix not in exts:
            allowed_str = ", ".join(f".{e}" for e in sorted(exts))
            raise ValueError(
                f"Extension '.{suffix}' is not in the attachment allowlist. "
                f"Allowed: {allowed_str}. "
                "Set MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS=* to allow all non-.md files."
            )
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

    def read_attachment(self, path: str) -> AttachmentContent:
        """Read the binary content of a non-.md attachment.

        Args:
            path: Relative attachment path (e.g. ``"assets/diagram.pdf"``).

        Returns:
            :class:`~markdown_vault_mcp.types.AttachmentContent` with
            base64-encoded content and MIME type.

        Raises:
            ValueError: If the path escapes the source directory, has an
                extension not in the allowlist, or the file does not exist.
            ValueError: If the file exceeds the configured size limit.
        """
        abs_path = self._validate_attachment_path(path)
        if not abs_path.is_file():
            raise ValueError(f"Attachment not found: {path}")

        stat = abs_path.stat()
        size_bytes = stat.st_size
        if self._max_attachment_size_mb > 0:
            limit_bytes = int(self._max_attachment_size_mb * 1024 * 1024)
            if size_bytes > limit_bytes:
                raise ValueError(
                    f"Attachment {path!r} is {size_bytes} bytes, which exceeds "
                    f"the limit of {self._max_attachment_size_mb} MB "
                    f"({limit_bytes} bytes). "
                    "Raise MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB or set it "
                    "to 0 to disable the limit."
                )

        mime_type, _ = mimetypes.guess_type(path)
        raw = abs_path.read_bytes()
        content_base64 = base64.b64encode(raw).decode("ascii")
        etag = compute_etag(raw)
        return AttachmentContent(
            path=path,
            mime_type=mime_type,
            size_bytes=size_bytes,
            content_base64=content_base64,
            modified_at=stat.st_mtime,
            etag=etag,
        )

    def write_attachment(
        self, path: str, content: bytes, if_match: str | None = None
    ) -> WriteResult:
        """Create or overwrite a non-.md attachment.

        Args:
            path: Relative attachment path (e.g. ``"assets/diagram.pdf"``).
            content: Raw bytes to write.
            if_match: Optional etag from a previous :meth:`read_attachment`
                call. When provided, the write is only performed if the
                current file hash matches this value, preventing overwrites
                of concurrent modifications. Pass ``None`` (default) to skip
                the check.

        Returns:
            :class:`~markdown_vault_mcp.types.WriteResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash.
            ValueError: If the path escapes the source directory, has an
                extension not in the allowlist, or the content exceeds the
                size limit.
        """
        self._check_writable()
        with self._write_lock:
            self._ensure_initialized()
            abs_path = self._validate_attachment_path(path)
            if if_match is not None:
                if not abs_path.is_file():
                    raise ConcurrentModificationError(
                        path, expected=if_match, actual="(file does not exist)"
                    )
                current_hash = compute_file_hash(abs_path)
                if current_hash != if_match:
                    raise ConcurrentModificationError(
                        path, expected=if_match, actual=current_hash
                    )
            if self._max_attachment_size_mb > 0:
                limit_bytes = int(self._max_attachment_size_mb * 1024 * 1024)
                if len(content) > limit_bytes:
                    raise ValueError(
                        f"Content ({len(content)} bytes) exceeds the limit of "
                        f"{self._max_attachment_size_mb} MB ({limit_bytes} bytes). "
                        "Raise MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB or set "
                        "it to 0 to disable the limit."
                    )
            created = not abs_path.is_file()
            abs_path.parent.mkdir(parents=True, exist_ok=True)
            abs_path.write_bytes(content)
            result = WriteResult(path=path, created=created)

        if self._on_write is not None:
            self._on_write(abs_path, "", "write")

        return result

    def write(
        self,
        path: str,
        content: str,
        frontmatter: dict | None = None,
        if_match: str | None = None,
    ) -> WriteResult:
        """Create or overwrite a document.

        Creates intermediate directories as needed.  If *frontmatter* is
        provided, it is serialised as a YAML header at the top of the file.

        Args:
            path: Relative document path.
            content: Markdown body (excluding frontmatter).
            frontmatter: Optional frontmatter dict serialised as YAML header.
            if_match: Optional etag from a previous :meth:`read` call.
                When provided, the write is only performed if the current
                file hash matches this value, preventing overwrites of
                concurrent modifications. Supplying *if_match* for a file
                that does not yet exist raises
                :exc:`~markdown_vault_mcp.exceptions.ConcurrentModificationError`.
                Pass ``None`` (default) to skip the check.

        Returns:
            :class:`~markdown_vault_mcp.types.WriteResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash (or the file does not exist).
            ValueError: If *path* escapes the source directory.
        """
        self._check_writable()
        with self._write_lock:
            self._ensure_initialized()

            abs_path = self._validate_path(path)
            if if_match is not None:
                if not abs_path.is_file():
                    raise ConcurrentModificationError(
                        path, expected=if_match, actual="(file does not exist)"
                    )
                current_hash = compute_file_hash(abs_path)
                if current_hash != if_match:
                    raise ConcurrentModificationError(
                        path, expected=if_match, actual=current_hash
                    )
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

            result = WriteResult(path=path, created=created)

        # Trigger callback outside lock to prevent deadlock.
        if self._on_write is not None:
            self._on_write(abs_path, file_content, "write")

        return result

    def edit(
        self, path: str, old_text: str, new_text: str, if_match: str | None = None
    ) -> EditResult:
        """Patch a section of a document.

        Reads the file, verifies *old_text* exists exactly once in the
        full file content (including frontmatter), replaces it with
        *new_text*, and writes back.

        Args:
            path: Relative document path.
            old_text: Text to replace (must appear exactly once).
            new_text: Replacement text.
            if_match: Optional etag from a previous :meth:`read` call.
                When provided, the edit is only performed if the current
                file hash matches this value, preventing edits based on
                stale content. Pass ``None`` (default) to skip the check.

        Returns:
            :class:`~markdown_vault_mcp.types.EditResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            DocumentNotFoundError: If the file does not exist.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash.
            EditConflictError: If *old_text* is not found or appears
                more than once.
        """
        self._check_writable()

        if not old_text:
            raise ValueError("old_text must not be empty")

        with self._write_lock:
            self._ensure_initialized()

            abs_path = self._validate_path(path)
            if not abs_path.is_file():
                raise DocumentNotFoundError(f"Document not found: {path}")

            if if_match is not None:
                current_hash = compute_file_hash(abs_path)
                if current_hash != if_match:
                    raise ConcurrentModificationError(
                        path, expected=if_match, actual=current_hash
                    )

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

        # Trigger callback outside lock to prevent deadlock.
        if self._on_write is not None:
            self._on_write(abs_path, new_content, "edit")

        return EditResult(path=path, replacements=1)

    def delete(self, path: str, if_match: str | None = None) -> DeleteResult:
        """Delete a document or attachment.

        Removes the file from disk.  For ``.md`` documents, also removes all
        FTS and embedding index entries.  For attachments, only the file is
        deleted (no index update).

        Args:
            path: Relative document or attachment path.
            if_match: Optional etag from a previous :meth:`read` or
                :meth:`read_attachment` call. When provided, the deletion is
                only performed if the current file hash matches this value.
                Pass ``None`` (default) to skip the check.

        Returns:
            :class:`~markdown_vault_mcp.types.DeleteResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            DocumentNotFoundError: If the file does not exist.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current file hash.
            ValueError: If the path escapes the source directory, or (for
                non-.md paths) has an extension not in the attachment allowlist.
        """
        self._check_writable()
        with self._write_lock:
            self._ensure_initialized()

            if path.endswith(".md"):
                abs_path = self._validate_path(path)
                if not abs_path.is_file():
                    raise DocumentNotFoundError(f"Document not found: {path}")
                if if_match is not None:
                    current_hash = compute_file_hash(abs_path)
                    if current_hash != if_match:
                        raise ConcurrentModificationError(
                            path, expected=if_match, actual=current_hash
                        )
                abs_path.unlink()
                self._fts.delete_by_path(path)
                if self._vectors is not None and self._embeddings_path is not None:
                    self._vectors.delete_by_path(path)
                    self._vectors.save(self._embeddings_path)
            else:
                abs_path = self._validate_attachment_path(path)
                if not abs_path.is_file():
                    raise DocumentNotFoundError(f"Attachment not found: {path}")
                if if_match is not None:
                    current_hash = compute_file_hash(abs_path)
                    if current_hash != if_match:
                        raise ConcurrentModificationError(
                            path, expected=if_match, actual=current_hash
                        )
                abs_path.unlink()

        # Trigger callback outside lock to prevent deadlock.
        if self._on_write is not None:
            self._on_write(abs_path, "", "delete")

        return DeleteResult(path=path)

    def rename(
        self, old_path: str, new_path: str, if_match: str | None = None
    ) -> RenameResult:
        """Rename or move a document or attachment.

        Renames the file on disk.  For ``.md`` documents, also updates FTS
        and embedding index entries.  For attachments, only the file is moved
        (no index update).  Creates intermediate directories for *new_path*
        as needed.

        Args:
            old_path: Current relative document or attachment path.
            new_path: Target relative document or attachment path.
            if_match: Optional etag from a previous :meth:`read` or
                :meth:`read_attachment` call for *old_path*. When provided,
                the rename is only performed if the current file hash matches
                this value. Pass ``None`` (default) to skip the check.

        Returns:
            :class:`~markdown_vault_mcp.types.RenameResult`.

        Raises:
            ReadOnlyError: If the collection is read-only.
            DocumentNotFoundError: If *old_path* does not exist.
            DocumentExistsError: If *new_path* already exists.
            ConcurrentModificationError: If *if_match* is provided and does
                not match the current hash of *old_path*.
            ValueError: If either path escapes the source directory, or (for
                non-.md paths) has an extension not in the attachment allowlist.
        """
        self._check_writable()
        with self._write_lock:
            self._ensure_initialized()

            if old_path.endswith(".md"):
                old_abs = self._validate_path(old_path)
                new_abs = self._validate_path(new_path)

                if not old_abs.is_file():
                    raise DocumentNotFoundError(f"Document not found: {old_path}")
                if new_abs.is_file():
                    raise DocumentExistsError(f"Target already exists: {new_path}")
                if if_match is not None:
                    current_hash = compute_file_hash(old_abs)
                    if current_hash != if_match:
                        raise ConcurrentModificationError(
                            old_path, expected=if_match, actual=current_hash
                        )

                new_abs.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_abs), str(new_abs))

                self._fts.delete_by_path(old_path)
                if self._vectors is not None:
                    self._vectors.delete_by_path(old_path)

                note = parse_note(new_abs, self._source_dir, self._chunk_strategy)
                self._fts.upsert_note(note)
                self._update_vector_index(note)

                callback_content = new_abs.read_text(encoding="utf-8")
            else:
                old_abs = self._validate_attachment_path(old_path)
                new_abs = self._validate_attachment_path(new_path)

                if not old_abs.is_file():
                    raise DocumentNotFoundError(f"Attachment not found: {old_path}")
                if new_abs.is_file():
                    raise DocumentExistsError(f"Target already exists: {new_path}")
                if if_match is not None:
                    current_hash = compute_file_hash(old_abs)
                    if current_hash != if_match:
                        raise ConcurrentModificationError(
                            old_path, expected=if_match, actual=current_hash
                        )

                new_abs.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(old_abs), str(new_abs))

                callback_content = ""

        # Trigger callback outside lock to prevent deadlock.
        if self._on_write is not None:
            self._on_write(new_abs, callback_content, "rename")

        return RenameResult(old_path=old_path, new_path=new_path)
