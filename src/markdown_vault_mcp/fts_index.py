"""SQLite FTS5 index for full-text search and tag filtering."""

from __future__ import annotations

import datetime
import json
import logging
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING, Any

from markdown_vault_mcp.types import FTSResult, ParsedNote

if TYPE_CHECKING:
    from collections.abc import Iterable

logger = logging.getLogger(__name__)


def _json_default(obj: Any) -> str:
    """Handle non-JSON-native types from YAML frontmatter.

    YAML parsers auto-detect date-like strings (e.g. ``2024-01-15``) as
    ``datetime.date`` or ``datetime.datetime`` objects, and bare time
    strings (e.g. ``15:30:00``) as ``datetime.time``.  This handler
    converts them to ISO-format strings so ``json.dumps()`` can
    serialise frontmatter without crashing.
    """
    if isinstance(obj, (datetime.datetime, datetime.date, datetime.time)):
        return obj.isoformat()
    raise TypeError(f"Object of type {type(obj).__name__} is not JSON serializable")


# DDL executed once on connection open.
_SCHEMA_SQL = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,
    title TEXT NOT NULL,
    folder TEXT NOT NULL DEFAULT '',
    frontmatter_json TEXT,
    content_hash TEXT NOT NULL,
    modified_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    heading TEXT,
    heading_level INTEGER NOT NULL,
    content TEXT NOT NULL,
    start_line INTEGER NOT NULL,
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS document_tags (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    tag_key TEXT NOT NULL,
    tag_value TEXT NOT NULL,
    UNIQUE(document_id, tag_key, tag_value),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tags_kv ON document_tags(tag_key, tag_value);
CREATE INDEX IF NOT EXISTS idx_tags_docid ON document_tags(document_id);

CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    path, title, folder, heading, content,
    tokenize='porter unicode61'
);
"""


def _escape_like(value: str) -> str:
    """Escape SQLite LIKE special characters in ``value``.

    SQLite LIKE treats ``%`` and ``_`` as wildcards and ``\\`` as the escape
    character (when ``ESCAPE '\\'`` is declared).  This function escapes all
    three so that a user-supplied folder name is matched literally.

    Args:
        value: Raw string that will be embedded in a LIKE pattern.

    Returns:
        String with ``\\``, ``%``, and ``_`` replaced by their escaped forms.
    """
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _derive_folder(path: str) -> str:
    """Derive the folder from a document's relative path.

    Args:
        path: Document relative path including ``.md`` extension,
            e.g. ``"Journal/2024-01-15.md"`` or ``"README.md"``.

    Returns:
        The parent directory as a forward-slash string, or ``""`` for
        documents at the collection root. Examples::

            "Journal/note.md"             -> "Journal"
            "Journal/2024/January/a.md"   -> "Journal/2024/January"
            "README.md"                   -> ""
    """
    parent = Path(path).parent
    # PurePosixPath('.') means no parent directory.
    folder = parent.as_posix()
    return "" if folder == "." else folder


def _open_connection(db_path: Path | str) -> sqlite3.Connection:
    """Open an SQLite connection with required pragmas and schema applied.

    Args:
        db_path: Filesystem path or ``":memory:"`` for an in-memory database.

    Returns:
        An open :class:`sqlite3.Connection` with the schema applied and
        ``foreign_keys`` enforcement active.
    """
    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Apply schema; executescript commits implicitly.
    conn.executescript(_SCHEMA_SQL)
    # Ensure foreign_keys stays ON for subsequent statements (executescript
    # does not guarantee this survives across statement boundaries in all
    # SQLite versions).
    conn.execute("PRAGMA foreign_keys = ON")
    # WAL mode allows concurrent readers during writes — essential for
    # search queries running while reindex or write operations update the DB.
    # In-memory databases do not support WAL; skip the pragma to avoid a
    # spurious warning (SQLite silently uses 'memory' journal mode).
    if str(db_path) != ":memory:":
        result = conn.execute("PRAGMA journal_mode = WAL").fetchone()
        if result is None or result[0].lower() != "wal":
            logger.warning(
                "Could not enable WAL journal mode (got %s); "
                "concurrent reads during writes may block",
                result[0] if result else "no result",
            )
    conn.commit()
    return conn


class FTSIndex:
    """SQLite FTS5 index providing BM25 search and tag filtering.

    Wraps a single SQLite database file (or in-memory database) and exposes
    CRUD operations and full-text search over a collection of markdown
    documents.

    Tag indexing behaviour is controlled by ``indexed_frontmatter_fields``:
    only the listed frontmatter keys are promoted into the ``document_tags``
    table for structured filtering.  Scalar values produce one row each; list
    values produce one row per item (deduplicated per document).  Complex
    types (nested dicts, objects) are stored in the raw JSON blob only and
    are not indexed.

    Args:
        db_path: Path to the SQLite database file.  Pass ``":memory:"`` (the
            default) for a transient in-memory database.
        indexed_frontmatter_fields: Frontmatter keys whose values are
            promoted to the ``document_tags`` table.  ``None`` means no tag
            indexing.
    """

    def __init__(
        self,
        db_path: Path | str = ":memory:",
        indexed_frontmatter_fields: list[str] | None = None,
    ) -> None:
        self._db_path = db_path
        self._indexed_fields: list[str] = indexed_frontmatter_fields or []
        self._conn = _open_connection(db_path)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _insert_document(
        self,
        cur: sqlite3.Cursor,
        note: ParsedNote,
        folder: str,
    ) -> int:
        """Insert a row into ``documents`` and return its new ``id``.

        Args:
            cur: Active cursor inside the current transaction.
            note: Parsed document to insert.
            folder: Pre-derived folder string.

        Returns:
            The ``ROWID`` / ``id`` of the newly inserted document row.

        Raises:
            RuntimeError: If the INSERT did not return a row ID.
        """
        cur.execute(
            """
            INSERT INTO documents (path, title, folder, frontmatter_json,
                                   content_hash, modified_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                note.path,
                note.title,
                folder,
                json.dumps(note.frontmatter, default=_json_default),
                note.content_hash,
                note.modified_at,
            ),
        )
        if cur.lastrowid is None:
            raise RuntimeError("INSERT did not return a row ID")
        return cur.lastrowid

    def _insert_sections(
        self,
        cur: sqlite3.Cursor,
        document_id: int,
        note: ParsedNote,
    ) -> None:
        """Insert all chunks for a document into ``sections``.

        Also inserts one row per chunk into the ``notes_fts`` virtual table.

        Args:
            cur: Active cursor inside the current transaction.
            document_id: The ``id`` of the parent document row.
            note: Parsed document whose chunks are to be inserted.
        """
        folder = _derive_folder(note.path)
        for chunk in note.chunks:
            cur.execute(
                """
                INSERT INTO sections (document_id, heading, heading_level,
                                      content, start_line)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    document_id,
                    chunk.heading,
                    chunk.heading_level,
                    chunk.content,
                    chunk.start_line,
                ),
            )
            cur.execute(
                """
                INSERT INTO notes_fts (path, title, folder, heading, content)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    note.path,
                    note.title,
                    folder,
                    chunk.heading or "",
                    chunk.content,
                ),
            )

    def _insert_tags(
        self,
        cur: sqlite3.Cursor,
        document_id: int,
        note: ParsedNote,
    ) -> None:
        """Index frontmatter values into ``document_tags``.

        Only keys listed in ``indexed_frontmatter_fields`` are processed.
        Scalar values produce one row; list values produce one row per item
        (deduplicated per document).  Complex types (dicts, etc.) are skipped.

        Args:
            cur: Active cursor inside the current transaction.
            document_id: The ``id`` of the parent document row.
            note: Parsed document whose frontmatter is to be indexed.
        """
        if not self._indexed_fields:
            return

        for key in self._indexed_fields:
            value = note.frontmatter.get(key)
            if value is None:
                continue

            if isinstance(value, list):
                # One row per item; skip non-scalar elements.
                seen: set[str] = set()
                for item in value:
                    if (
                        isinstance(item, (str, int, float, bool))
                        and not isinstance(item, bool)
                    ) or isinstance(item, bool):
                        # Accept all scalar types.
                        tag_val = str(item)
                        if tag_val not in seen:
                            seen.add(tag_val)
                            cur.execute(
                                """
                                INSERT OR IGNORE INTO document_tags
                                    (document_id, tag_key, tag_value)
                                VALUES (?, ?, ?)
                                """,
                                (document_id, key, tag_val),
                            )
            elif isinstance(value, dict):
                # Complex type — skip.
                logger.debug(
                    "Skipping complex frontmatter value for key %r in %s",
                    key,
                    note.path,
                )
            else:
                # Scalar value.
                cur.execute(
                    """
                    INSERT OR IGNORE INTO document_tags
                        (document_id, tag_key, tag_value)
                    VALUES (?, ?, ?)
                    """,
                    (document_id, key, str(value)),
                )

    def _delete_document(self, cur: sqlite3.Cursor, path: str) -> int:
        """Delete a document row (cascade deletes sections and tags).

        Also removes all FTS rows for the document's path.

        Args:
            cur: Active cursor inside the current transaction.
            path: Relative document path.

        Returns:
            Number of document rows deleted (0 or 1).
        """
        cur.execute("DELETE FROM notes_fts WHERE path = ?", (path,))
        cur.execute("DELETE FROM documents WHERE path = ?", (path,))
        return cur.rowcount

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def build_from_notes(self, notes: Iterable[ParsedNote]) -> int:
        """Bulk-insert all notes, replacing any existing data.

        Existing documents are upserted (delete + re-insert) so
        ``build_from_notes`` is idempotent when called on an already-populated
        index.  All inserts are wrapped in a single transaction for
        performance and atomicity.

        Args:
            notes: Iterable of parsed documents to index.

        Returns:
            Total number of chunks (sections) indexed.
        """
        total_chunks = 0
        with self._conn:
            cur = self._conn.cursor()
            for note in notes:
                folder = _derive_folder(note.path)
                self._delete_document(cur, note.path)
                doc_id = self._insert_document(cur, note, folder)
                self._insert_sections(cur, doc_id, note)
                self._insert_tags(cur, doc_id, note)
                total_chunks += len(note.chunks)
                logger.debug(
                    "build_from_notes: indexed %d chunks for %s",
                    len(note.chunks),
                    note.path,
                )
        logger.info("build_from_notes: indexed %d chunks total", total_chunks)
        return total_chunks

    def upsert_note(self, note: ParsedNote) -> int:
        """Insert or replace a single document in the index.

        Deletes any existing rows for ``note.path``, then inserts the
        document, its sections, and its tags in a single transaction.

        Args:
            note: Parsed document to insert or replace.

        Returns:
            Number of chunks (sections) indexed for this document.
        """
        folder = _derive_folder(note.path)
        with self._conn:
            cur = self._conn.cursor()
            self._delete_document(cur, note.path)
            doc_id = self._insert_document(cur, note, folder)
            self._insert_sections(cur, doc_id, note)
            self._insert_tags(cur, doc_id, note)
        logger.debug(
            "upsert_note: indexed %d chunks for %s", len(note.chunks), note.path
        )
        return len(note.chunks)

    def delete_by_path(self, path: str) -> int:
        """Remove a document and all its data from the index.

        Args:
            path: Relative document path (e.g. ``"Journal/note.md"``).

        Returns:
            Number of document rows deleted (0 if path was not indexed).
        """
        with self._conn:
            cur = self._conn.cursor()
            deleted = self._delete_document(cur, path)
        if deleted:
            logger.debug("delete_by_path: removed %s", path)
        return deleted

    def search(
        self,
        query: str,
        *,
        limit: int = 10,
        folder: str | None = None,
        filters: dict[str, str] | None = None,
    ) -> list[FTSResult]:
        """Full-text search using BM25 ranking.

        Optionally filters results by folder prefix and/or frontmatter tag
        key-value pairs.  Each entry in ``filters`` is ANDed.

        Args:
            query: FTS5 query string.
            limit: Maximum number of results to return.
            folder: If provided, only return documents whose ``folder``
                starts with this string.
            filters: Dict of ``{tag_key: tag_value}`` pairs.  All pairs must
                match (AND semantics).

        Returns:
            List of :class:`~markdown_vault_mcp.types.FTSResult` objects ordered by
            descending BM25 score.
        """
        # Build tag subquery filters (one per entry, ANDed).
        tag_clauses: list[str] = []
        tag_params: list[str] = []
        if filters:
            for key, value in filters.items():
                tag_clauses.append(
                    "d.id IN ("
                    "  SELECT document_id FROM document_tags"
                    "  WHERE tag_key = ? AND tag_value = ?"
                    ")"
                )
                tag_params.extend([key, value])

        folder_clause = ""
        folder_params: list[str] = []
        if folder is not None:
            # Match exact folder or sub-folders.  Escape LIKE wildcards in the
            # user-supplied folder value so that literal '%' and '_' characters
            # are matched as-is rather than treated as SQL wildcards.
            escaped = _escape_like(folder)
            folder_clause = "AND (d.folder = ? OR d.folder LIKE ? ESCAPE '\\')"
            folder_params = [folder, escaped + "/%"]

        tag_filter_sql = ""
        if tag_clauses:
            tag_filter_sql = "AND " + " AND ".join(tag_clauses)

        sql = f"""
            SELECT
                f.path,
                d.title,
                d.folder,
                f.heading,
                f.content,
                ABS(f.rank) AS score
            FROM notes_fts f
            JOIN documents d ON d.path = f.path
            WHERE notes_fts MATCH ?
              {folder_clause}
              {tag_filter_sql}
            ORDER BY score DESC
            LIMIT ?
        """

        params: list[object] = [query, *folder_params, *tag_params, limit]
        logger.debug(
            "FTS search: query=%r folder=%r filters=%r limit=%d",
            query, folder, filters, limit,
        )
        cur = self._conn.execute(sql, params)
        rows = cur.fetchall()
        logger.debug("FTS search: %d results for query=%r", len(rows), query)

        results: list[FTSResult] = []
        for row in rows:
            results.append(
                FTSResult(
                    path=row["path"],
                    title=row["title"],
                    folder=row["folder"],
                    heading=row["heading"] or None,
                    content=row["content"],
                    score=row["score"],
                )
            )
        return results

    def get_note(self, path: str) -> dict | None:
        """Return document metadata for a single note.

        Args:
            path: Relative document path.

        Returns:
            A dict with keys ``path``, ``title``, ``folder``,
            ``frontmatter_json``, ``content_hash``, ``modified_at``, or
            ``None`` if the document is not indexed.
        """
        cur = self._conn.execute(
            """
            SELECT path, title, folder, frontmatter_json,
                   content_hash, modified_at
            FROM documents
            WHERE path = ?
            """,
            (path,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        return dict(row)

    def list_notes(self, *, folder: str | None = None) -> list[dict]:
        """List all indexed documents, optionally filtered by folder.

        Args:
            folder: If provided, only return documents whose ``folder``
                matches exactly or is a sub-folder of this value.

        Returns:
            List of dicts with the same shape as :meth:`get_note`.
        """
        if folder is not None:
            # Escape LIKE wildcards in the user-supplied folder value so that
            # literal '%' and '_' characters are matched as-is.
            escaped = _escape_like(folder)
            cur = self._conn.execute(
                """
                SELECT path, title, folder, frontmatter_json,
                       content_hash, modified_at
                FROM documents
                WHERE folder = ? OR folder LIKE ? ESCAPE '\\'
                ORDER BY path
                """,
                (folder, escaped + "/%"),
            )
        else:
            cur = self._conn.execute(
                """
                SELECT path, title, folder, frontmatter_json,
                       content_hash, modified_at
                FROM documents
                ORDER BY path
                """
            )
        return [dict(row) for row in cur.fetchall()]

    def list_folders(self) -> list[str]:
        """Return all distinct folder values across the index.

        Returns:
            Sorted list of folder strings (including ``""`` for the root).
        """
        cur = self._conn.execute(
            "SELECT DISTINCT folder FROM documents ORDER BY folder"
        )
        return [row[0] for row in cur.fetchall()]

    def list_field_values(self, field: str) -> list[str]:
        """Return all distinct tag values for a given frontmatter field.

        If ``field`` was not in ``indexed_frontmatter_fields``, returns an
        empty list.

        Args:
            field: Frontmatter key (e.g. ``"cluster"``).

        Returns:
            Sorted list of distinct value strings.
        """
        cur = self._conn.execute(
            """
            SELECT DISTINCT tag_value
            FROM document_tags
            WHERE tag_key = ?
            ORDER BY tag_value
            """,
            (field,),
        )
        return [row[0] for row in cur.fetchall()]

    def count_chunks(self) -> int:
        """Return the total number of chunk rows in the ``sections`` table.

        Returns:
            Integer count of all indexed chunks across all documents.
        """
        row = self._conn.execute("SELECT COUNT(*) FROM sections").fetchone()
        return row[0] if row else 0

    def get_toc(self, path: str) -> list[dict[str, str | int]]:
        """Return headings for a document, ordered by position.

        Queries the sections table for distinct non-NULL headings, ordered by
        the first row that introduces each heading.

        Args:
            path: Relative document path (e.g. ``"Journal/note.md"``).

        Returns:
            List of ``{"heading": str, "level": int}`` dicts ordered by
            first appearance.  Empty list if the document is not found or
            has no headings.
        """
        cur = self._conn.execute(
            """
            SELECT heading, heading_level
            FROM sections
            WHERE document_id = (SELECT id FROM documents WHERE path = ?)
              AND heading IS NOT NULL
            GROUP BY heading, heading_level
            ORDER BY MIN(rowid)
            """,
            (path,),
        )
        return [
            {"heading": row["heading"], "level": row["heading_level"]}
            for row in cur.fetchall()
        ]

    def close(self) -> None:
        """Close the underlying database connection.

        After calling this method, the index must not be used.
        """
        self._conn.close()
        logger.debug("FTSIndex closed (db_path=%s)", self._db_path)
