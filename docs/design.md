# markdown-vault-mcp: Design Specification v2

> Generic markdown collection MCP server with FTS5 + semantic search,
> frontmatter-aware indexing, and incremental reindexing. Extracted from
> and replacing the search layer in `pvliesdonk/if-craft-corpus`.

## Terminology

This spec uses the following terms consistently:

- **Document**: a single `.md` file in the collection. The primary term used
  throughout this spec.
- **Folder**: a subdirectory within `source_dir`, represented as a
  `/`-separated relative path (e.g., `Journal/2024`). The root of `source_dir`
  is represented as an empty string `""`.
- **Chunk**: a portion of a document, typically a section under a heading.
  Stored in the `sections` database table.
- **Tag**: a key-value pair from document frontmatter, stored in the
  `document_tags` table for indexed filtering.

In code: `ParsedNote` (scanner output), `Chunk` (section of a document),
`sections` (database table name).

## Problem

`pvliesdonk/if-craft-corpus` has a well-tested search stack (FTS5 + vector
embeddings + FastMCP server) that is coupled to the IF corpus domain. The same
infrastructure is needed for serving an Obsidian vault (or any directory of
markdown files) over MCP. Rather than duplicating code, extract the generic
layer into a reusable package. The corpus becomes just one instance; a personal
vault becomes another.

## Use Cases

1. **Obsidian vault** (`pvliesdonk/obsidian.md`, private): personal knowledge
   base served over MCP with read/write support and optional git-backed sync.
2. **IF Craft Corpus** (`pvliesdonk/if-craft-corpus`): read-only curated
   collection with domain-specific tools, strict frontmatter requirements.
3. **Python library**: direct use as a search API (e.g., wrapped as a LangChain
   tool by downstream projects like QuestFoundry). The `Collection` class is
   the primary interface; MCP is one consumer, not the only one. Other
   frameworks (LangChain, LlamaIndex, etc.) may wrap `Collection` directly.

## Architecture

Two packages, one dependency edge (eventual):

```
markdown-vault-mcp (new package)
+-- scanner.py        -- file discovery, frontmatter parsing, chunking
+-- fts_index.py      -- SQLite FTS5 schema, BM25 search
+-- vector_index.py   -- numpy embeddings, cosine similarity
+-- providers.py      -- Ollama / OpenAI / SentenceTransformers
+-- tracker.py        -- hash-based change detection
+-- collection.py     -- thin facade: init, lazy loading, public API
+-- config.py         -- configuration loading
+-- mcp_server.py     -- generic FastMCP server
+-- cli.py            -- CLI entry point

ifcraftcorpus (existing, refactored later)
+-- depends on markdown-vault-mcp
+-- ships corpus/ content
+-- adds domain-specific tools (search_exemplars, list_exemplar_tags)
+-- adds subagent prompts
+-- thin wrapper: configures Collection with required_frontmatter
```

**ifcraftcorpus stays as-is** during markdown-vault-mcp development. No changes to
the existing package until a complete refactor after markdown-vault-mcp is stable.

## Reference Code

All code below lives in `pvliesdonk/if-craft-corpus`. Read these files for
implementation patterns:

| File | Reuse Strategy | Notes |
|------|----------------|-------|
| `providers.py` | **Copy + adapt** | Rename env var prefix `IFCRAFTCORPUS_` to `MARKDOWN_VAULT_MCP_`. Fix hardcoded imports. |
| `embeddings.py` | **Copy + adapt** | Rename to `vector_index.py`. The `load()` classmethod contains a hardcoded `from ifcraftcorpus.providers import get_embedding_provider` -- this **must** be changed to `from markdown_vault_mcp.providers import get_embedding_provider` or it will raise `ImportError` at runtime. |
| `search.py` | **Adapt** | Pattern for `Collection` facade. Replace domain methods with generic API. |
| `index.py` | **Adapt** | Pattern for `fts_index.py`. Replace corpus-specific schema. Fix hybrid score bug (see RRF section). |
| `parser.py` | **Replace** | Replace with generic frontmatter + heading-based chunking using `python-frontmatter`. |
| `mcp_server.py` | **Adapt** | Replace domain tools with generic tools. Use lifespan hooks instead of lazy global singleton. |
| `cli.py` | **Adapt** | Simplify for markdown-vault-mcp. |

**Reuse strategy definitions**:
- **Copy + adapt**: copy the file as a starting point, then modify for the new
  package. Temporary code duplication accepted until ifcraftcorpus refactor.
- **Adapt**: use as a design reference; rewrite for the new package.
- **Replace**: discard and write new implementation.

## Core Design Decisions

### Document Identity

Documents are identified by their **relative path from the collection root**,
including the `.md` extension. Example: `Journal/2024-01-15.md`.

This avoids collisions between files with the same stem in different
directories (e.g., `Journal/2024-01-15.md` vs `Archive/2024-01-15.md`).

### Folder Derivation

The `folder` field is derived as the parent directory of the document's
relative path:

- `README.md` -> folder `""`  (root)
- `Journal/2024-01-15.md` -> folder `"Journal"`
- `Journal/2024/January/note.md` -> folder `"Journal/2024/January"`

`list_folders()` returns all distinct folder values across the collection.

### Frontmatter Handling

Frontmatter is **optional by default**. Documents without frontmatter are
indexed normally with an empty metadata dict. Title defaults to the first H1
heading, then the filename (without extension).

A `required_frontmatter` configuration option enforces specific fields:

```python
Collection(
    source_dir=Path("corpus/"),
    required_frontmatter=["title", "cluster"],
)
```

- `None` (default): all `.md` files are indexed regardless of frontmatter.
- `["title", "cluster"]`: documents missing any listed field are excluded from
  the index entirely and will not be searchable. At scan completion, the
  number of skipped documents is logged at `INFO` level.

### Frontmatter Filtering

Hybrid approach:

1. **`document_tags` table** (indexed) for structured filtering. An
   `indexed_frontmatter_fields` config option specifies which frontmatter keys
   get promoted into `(tag_key, tag_value)` rows. Each unique
   `(document_id, tag_key, tag_value)` tuple produces one row (duplicates in
   source lists are deduplicated). Complex types (nested dicts, objects) in
   frontmatter are stored in the JSON blob but are **not** indexed -- only
   scalar and simple list values are indexed.
2. **Raw frontmatter JSON blob** stored in the `documents` table for display
   and retrieval only -- not queried via index.

The `filters` parameter on `search()` generates
`document_id IN (SELECT ... FROM document_tags WHERE ...)` subqueries. This
gives O(1) indexed lookup without `json_extract()` performance problems.

**Filter semantics**: `filters` is `dict[str, str]`. Each key-value pair is
ANDed. Example: `filters={"cluster": "fiction", "genre": "horror"}` returns
documents tagged with both `cluster=fiction` AND `genre=horror`. Multi-valued
OR queries within a single key are not supported in Phase 1; use multiple
searches and merge client-side.

### FTS5 Schema

See the [Database Schema](#database-schema) section for full DDL.

Generic columns replacing the corpus-specific `cluster`/`topics`:
`path`, `title`, `folder`, `heading`, `content`.

Domain-specific filtering (by cluster, topic, tag) happens via the
`document_tags` table, not FTS5 columns.

### Hybrid Search: Reciprocal Rank Fusion

BM25 scores (0-20+) and cosine similarity (0-1) are on incompatible scales.
Raw comparison is a latent bug carried from ifcraftcorpus.

**Solution**: Reciprocal Rank Fusion (RRF). Each result set is ranked
independently. Merged score: `1 / (k + rank)` where `k` is a constant
(typically 60). Results are sorted by summed RRF score.

This produces sensible merged rankings regardless of the raw score scales.

### Chunking Strategy

A `ChunkStrategy` protocol enables extensible chunking:

```python
@runtime_checkable
class ChunkStrategy(Protocol):
    def chunk(self, content: str, metadata: dict[str, Any]) -> list[Chunk]:
        """Chunk the markdown body into sections.

        Args:
            content: Markdown body after frontmatter has been stripped.
            metadata: Parsed frontmatter dict (for context, not modification).

        Returns:
            List of Chunk objects.
        """
        ...
```

**Phase 1 implementations**:
- `HeadingChunker`: split on H1/H2 boundaries. Short documents stay as single
  chunk. Each chunk inherits the document's frontmatter. Default.
- `WholeDocumentChunker`: one chunk per document. Good for short documents.

**Future** (deferred):
- `SlidingWindowChunker`: fixed-size overlapping windows with configurable
  tokenizer.

The `Collection` config accepts `chunk_strategy: str | ChunkStrategy` -- string
for built-in names, or pass a custom instance.

### Change Tracking

**Hash-based**, not git-based. Works with any directory, no git dependency.

- **State file** (the JSON persistence layer for hash-based change detection):
  `{relative_path: sha256_hash}` as JSON.
- **Default path**: `{source_dir}/.markdown_vault_mcp/state.json` (when
  `state_path=None`).
- On `reindex()`: scan all files, compare hashes to stored state, re-parse and
  re-embed only changed/added files, remove deleted entries.

**Trigger model**: startup scan + explicit `reindex` tool call. No background
polling in Phase 1. Architecture supports adding `watch_interval` or watchdog
integration later without refactoring.

### Incremental Reindex

Full numpy array rebuild on every reindex (filter unchanged rows + append new).
Only changed files are re-embedded (the expensive API call part). This is
correct and simple at vault scale (even 10k chunks at 768 dimensions is ~60MB).

The `VectorIndex` maintains a sidecar metadata list mapping each row index to
its source document path, enabling bulk deletion when a document is reindexed.

### Index Lifecycle

Two methods manage the index:

- **`build_index(force=False)`**: initial population. Scans `source_dir` and
  builds the FTS index. If the index already has data and `force=False`, this
  is a no-op. `force=True` drops and rebuilds from scratch.
- **`reindex()`**: incremental update. Uses `ChangeTracker` to detect
  adds/modifies/deletes since the last scan and applies only the delta.

**Lazy initialization**: on first call to `search()`, `list()`, or `read()`,
`Collection` lazily builds the FTS index from `source_dir` if no pre-built
`index_path` was provided. `build_index()` can be called explicitly to
pre-warm the index or to force a rebuild.

### Error Handling

Two-layer model:

- **Library layer** (`Collection`, `FTSIndex`, `VectorIndex`, etc.): raises
  specific exceptions. Callers catch and handle.
- **MCP tool layer**: catches exceptions, returns structured error responses
  per FastMCP conventions.

**Exception types**:

| Exception | Raised by | When |
|-----------|-----------|------|
| `DocumentNotFoundError` | `edit()`, `delete()`, `rename()` | Document path does not exist on disk |
| `ReadOnlyError` | `write()`, `edit()`, `delete()`, `rename()` | `read_only=True` |
| `EditConflictError` | `edit()` | `old_text` not found or appears more than once |
| `DocumentExistsError` | `rename()` | `new_path` already exists |
| `ConcurrentModificationError` | `write()`, `edit()`, `delete()`, `rename()`, `write_attachment()` | `if_match` provided and current file hash does not match |
| `ValueError` | `build_embeddings()` | No `embedding_provider` or `embeddings_path` configured |
| `None` return | `read()` | Path escapes `source_dir` (traversal attempt) or file does not exist on disk |
| `ValueError` | `edit()` | `old_text` is empty string |

`build_embeddings()` processes chunks in bounded batches (default 64) to avoid
pathological memory allocation from embedding providers (see issue #159).
FastEmbed's ONNX inference uses a further inner batch size of 4 to keep
per-call memory bounded — without this, the ONNX attention matrix for 64 long
chunks can require >192 GB of allocation. The save happens once at the end so a
mid-run crash does not leave a partial index that the skip-if-exists check
treats as complete on the next startup.

### Thread Safety

`Collection` serialises all write operations with a `threading.Lock`. The lock
is held for the duration of each write (disk write + FTS index update), then
released **before** deferred operations are submitted.

**Deferred operations** (issue #175): both embedding re-computation and the
`on_write` callback (git commit) are deferred to background threads so write
methods return immediately after the file write + FTS update (~5ms total
instead of seconds). Specifically:

- **Embedding updates**: modified document paths are added to a dirty set.
  A background timer flushes the set every 30 seconds, re-embedding all dirty
  documents and saving the `.npy` file once per batch. The flush also runs
  synchronously before semantic/hybrid search (to ensure consistent results)
  and on `close()` (to prevent data loss).
- **`on_write` callback** (git commit): submitted to a background worker
  thread via a queue. The `GitWriteStrategy._lock` preserves commit ordering.
  The queue is drained on `close()`.

The contract is:

- Concurrent reads are safe without locking.
- Concurrent writes are serialised.
- `reindex()` acquires the write lock for its mutation phase (FTS upserts,
  vector updates, tracker save). The filesystem scan phase runs outside the
  lock to minimise lock hold time.
- The `on_write` callback fires in a **background thread** — it must not
  itself call write methods on the same Collection instance (deadlock).
- Callbacks must not raise; exceptions are logged and swallowed.

#### Optimistic Concurrency (`if_match`)

All five write methods (`write()`, `edit()`, `delete()`, `rename()`,
`write_attachment()`) accept an optional `if_match: str | None = None`
parameter.  When provided, the method computes the SHA-256 hex digest of the
current file **inside the write lock** and compares it to `if_match`.  If
the digests differ, `ConcurrentModificationError` is raised and no mutation
occurs.  Passing `if_match=None` (the default) skips the check and preserves
pre-existing unconditional-write behavior.

The etag used for comparison is the same value returned in the `etag` field of
`read()` and `read_attachment()` responses, so the round-trip pattern is:

```python
note = collection.read("doc.md")
collection.write("doc.md", new_content, if_match=note.etag)
```

### Security: Path Traversal Protection

All public **write** methods accepting a `path` parameter call
`Collection._validate_path()` before any disk I/O. This method:

1. Resolves the path to an absolute path via `Path.resolve()`.
2. Checks the resolved path is within `source_dir` via `is_relative_to()`.
3. Raises `ValueError("Path traversal detected: ...")` if it escapes.

This applies to `write()`, `edit()`, `delete()`, `rename()`, and all
attachment write operations.

`read()` validates the path inline rather than via `_validate_path()`: if the
resolved path escapes `source_dir`, it returns `None` instead of raising.

### Lifecycle: Collection.close()

`Collection.close()` must be called on shutdown to release resources:

1. Flushes any deferred embedding updates (re-embeds dirty documents, saves `.npy`).
2. Drains the background write-callback queue (waits for pending git commits).
3. Closes the `GitWriteStrategy` (flushes and pushes pending commits).
4. Closes the SQLite database connection.

This ensures no work is lost on shutdown. The full lifecycle contract is:

```
Collection(...)
  → sync_from_remote_before_index()   # git fetch + ff-only before first index
  → build_index()                     # build FTS index
  → build_embeddings()                # build vector index (when configured)
  → start()                           # launch background pull loop
  → zero or more read/write operations
  → close()                           # stop pull loop, flush git, release SQLite
```

`stop()` may also be called independently to pause the pull loop without closing
the collection (e.g. during maintenance or test teardown). It is a no-op if the
loop was never started.

In the MCP server, `close()` is called in the FastMCP lifespan `finally` block.
Callers using `Collection` as a Python library must call `close()` explicitly
(or use it as a context manager if one is added in future).

### Concurrency

The library is **synchronous** internally. This is appropriate for the
single-user vault use case and for Python library consumers (LangChain tools
are typically sync).

In the MCP server layer, use `asyncio.to_thread(collection.search, ...)` for
tool handlers to avoid blocking the FastMCP event loop.

**Future work**: async embedding provider path for non-blocking batch
operations.

### Logging

Follow FastMCP conventions and standard Python logging:
`logging.getLogger(__name__)` throughout. No `print()` for operational output.

## Data Types

All public return types and major internal structures:

```python
from dataclasses import dataclass, field
from typing import Any, Literal

# --- Scanner types ---

@dataclass
class ParsedNote:
    """A parsed markdown document."""
    path: str                         # relative to source_dir, includes .md
    frontmatter: dict[str, Any]       # parsed YAML frontmatter (empty dict if none)
    title: str                        # from frontmatter, first H1, or filename
    chunks: list[Chunk]               # content chunks
    content_hash: str                 # SHA256 of raw file content
    modified_at: float                # file mtime

@dataclass
class Chunk:
    """A chunk of a document, typically a section under a heading."""
    heading: str | None               # heading text, None for preamble
    heading_level: int                # 0 for preamble, 1-6 for headings
    content: str                      # markdown body (frontmatter stripped)
    start_line: int                   # line number in source file

# --- Search types ---

@dataclass
class SearchResult:
    """A search result from the Collection API."""
    path: str                         # document relative path
    title: str                        # document title
    folder: str                       # derived folder
    heading: str | None               # matched section heading (None for summary)
    content: str                      # matched text content
    score: float                      # relevance score (RRF in hybrid mode)
    search_type: Literal["keyword", "semantic"]
    frontmatter: dict[str, Any]       # document frontmatter

@dataclass
class FTSResult:
    """A raw search result from the FTS5 index layer."""
    path: str
    title: str
    folder: str
    heading: str | None
    content: str
    score: float                      # BM25 score (abs value)

# --- CRUD types ---

@dataclass
class NoteContent:
    """Full content of a document, returned by read()."""
    path: str
    title: str
    folder: str
    content: str                      # raw markdown (including frontmatter)
    frontmatter: dict[str, Any]
    modified_at: float
    etag: str                         # SHA256 hex of raw file bytes; use as if_match

@dataclass
class NoteInfo:
    """Summary info for a document, returned by list_documents()."""
    path: str
    title: str
    folder: str
    frontmatter: dict[str, Any]
    modified_at: float
    kind: str = "note"                # always "note" for markdown documents

@dataclass
class WriteResult:
    """Result of a write operation."""
    path: str
    created: bool                     # True if new file, False if overwrite

@dataclass
class EditResult:
    """Result of an edit operation."""
    path: str
    replacements: int                 # always 1 (enforced by edit semantics)

@dataclass
class DeleteResult:
    """Result of a delete operation."""
    path: str

@dataclass
class RenameResult:
    """Result of a rename operation."""
    old_path: str
    new_path: str

# --- Index types ---

@dataclass
class IndexStats:
    """Statistics from build_index()."""
    documents_indexed: int
    chunks_indexed: int
    skipped: int                      # documents skipped (required_frontmatter)

@dataclass
class ReindexResult:
    """Result of an incremental reindex."""
    added: int
    modified: int
    deleted: int
    unchanged: int

@dataclass
class CollectionStats:
    """Collection-wide statistics."""
    document_count: int
    chunk_count: int
    folder_count: int
    semantic_search_available: bool
    indexed_frontmatter_fields: list[str]

# --- Change tracking ---

@dataclass
class ChangeSet:
    """Documents that changed since last index."""
    added: list[str]
    modified: list[str]
    deleted: list[str]
    unchanged: int
```

## Database Schema

Full DDL for the SQLite database used by `FTSIndex`:

```sql
-- Documents table: one row per .md file
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY,
    path TEXT UNIQUE NOT NULL,        -- relative path (document identity)
    title TEXT NOT NULL,
    folder TEXT NOT NULL DEFAULT '',  -- derived from path parent
    frontmatter_json TEXT,            -- raw YAML frontmatter as JSON (for display)
    content_hash TEXT NOT NULL,       -- SHA256 of raw file content
    modified_at REAL NOT NULL         -- file mtime
);

-- Sections table: one row per chunk within a document
CREATE TABLE IF NOT EXISTS sections (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    heading TEXT,                     -- heading text, NULL for preamble
    heading_level INTEGER NOT NULL,   -- 0 for preamble, 1-6 for headings
    content TEXT NOT NULL,            -- chunk content (frontmatter stripped)
    start_line INTEGER NOT NULL,      -- line number in source file
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

-- Document tags: indexed frontmatter key-value pairs
CREATE TABLE IF NOT EXISTS document_tags (
    id INTEGER PRIMARY KEY,
    document_id INTEGER NOT NULL,
    tag_key TEXT NOT NULL,
    tag_value TEXT NOT NULL,
    UNIQUE(document_id, tag_key, tag_value),
    FOREIGN KEY (document_id) REFERENCES documents(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tags_kv
    ON document_tags(tag_key, tag_value);

CREATE INDEX IF NOT EXISTS idx_tags_docid
    ON document_tags(document_id);

-- FTS5 virtual table for full-text search
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    path,
    title,
    folder,
    heading,
    content,
    tokenize='porter unicode61'
);
```

## Module Design

### `collection.py` -- Thin Facade

The main interface. Orchestrates specialized modules. Target: ~200 lines.

```python
class Collection:
    def __init__(
        self,
        *,
        source_dir: Path,
        index_path: Path | None = None,       # None = in-memory SQLite
        embeddings_path: Path | None = None,  # None = semantic search disabled
        embedding_provider: EmbeddingProvider | None = None,
        read_only: bool = True,
        state_path: Path | None = None,       # None = {source_dir}/.markdown_vault_mcp/state.json
        indexed_frontmatter_fields: list[str] | None = None,
        required_frontmatter: list[str] | None = None,
        chunk_strategy: str | ChunkStrategy = "heading",
        on_write: WriteCallback | None = None,
        exclude_patterns: list[str] | None = None,
    ): ...

    # --- Search ---
    def search(
        self, query: str, *, limit: int = 10,
        mode: Literal["keyword", "semantic", "hybrid"] = "keyword",
        filters: dict[str, str] | None = None,
        folder: str | None = None,
    ) -> list[SearchResult]: ...

    # --- Read/Write (mirrors LLM file tool semantics) ---
    def read(self, path: str) -> NoteContent | None: ...
    def write(self, path: str, content: str,
              frontmatter: dict | None = None,
              if_match: str | None = None) -> WriteResult: ...
    def edit(self, path: str, old_text: str, new_text: str,
             if_match: str | None = None) -> EditResult: ...
    def delete(self, path: str,
               if_match: str | None = None) -> DeleteResult: ...
    def rename(self, old_path: str, new_path: str,
               if_match: str | None = None) -> RenameResult: ...
    def list(self, *, folder: str | None = None,
             pattern: str | None = None) -> list[NoteInfo]: ...

    # --- Index management ---
    def build_index(self, *, force: bool = False) -> IndexStats: ...
    def reindex(self) -> ReindexResult: ...
    def build_embeddings(self, *, force: bool = False) -> int: ...
    def embeddings_status(self) -> dict: ...

    # --- Metadata ---
    def list_folders(self) -> list[str]: ...
    def list_tags(self, field: str = "tags") -> list[str]: ...
    def stats(self) -> CollectionStats: ...
```

**Constructor defaults**:
- `index_path=None`: index is created in-memory (`:memory:` SQLite). If
  provided, persisted to disk.
- `embeddings_path=None`: semantic search is disabled.
- `state_path=None`: defaults to `{source_dir}/.markdown_vault_mcp/state.json`.

**Lazy initialization**: on first call to `search()`, `list()`, or `read()`,
`Collection` lazily builds the FTS index from `source_dir` if no pre-built
`index_path` was provided.

**Write operations** (`write`, `edit`, `delete`, `rename`) raise
`ReadOnlyError` when `read_only=True`.

**`write()` behavior**: creates or overwrites the document at `path`. Creates
intermediate directories as needed (`mkdir -p` semantics). If `frontmatter` is
provided, it is serialized as YAML front matter at the top of the file. Updates
the FTS index and triggers `on_write`.

**`edit()` behavior**: reads the file first, verifies `old_text` exists exactly
once in the full file content (including frontmatter). Replaces it with
`new_text`, writes back, updates index, triggers `on_write`. Raises
`DocumentNotFoundError` if the file does not exist. Raises `EditConflictError`
if `old_text` is not found or appears more than once.

**`delete()` behavior**: removes the file from disk, deletes FTS and embedding
entries, triggers `on_write`. Raises `DocumentNotFoundError` if not found.

**`rename()` behavior** (Phase 2-3): renames the file on disk, deletes old
FTS/embedding entries, inserts new entries under the new path, updates
embedding metadata in-place. Triggers `on_write` with the new path. Raises
`DocumentNotFoundError` if `old_path` does not exist. Raises
`DocumentExistsError` if `new_path` already exists.

**`list()` pattern parameter**: if provided, `pattern` is a Unix glob matched
against the relative path using `fnmatch.fnmatch()`. Example:
`pattern="Journal/*.md"` returns only documents in the Journal folder.

**`list_tags(field)` behavior**: queries only the `document_tags` table. If
`field` was not in `indexed_frontmatter_fields`, returns `[]`.

**`on_write` callback**:

```python
WriteCallback = Callable[[Path, str, Literal["write", "edit", "delete", "rename"]], None]
```

- `path`: absolute path on disk.
  - For `write`, `edit`: the file's final path after the operation.
  - For `rename`: the **new** path (old path is gone).
  - For `delete`: the path before deletion (file no longer exists on disk).
- `content`: updated file content as a string.
  - For `delete`: empty string `""` (file no longer exists).
  - For `rename` of a note (`.md`): the full file content at the new path.
  - For `rename` of an attachment: empty string `""` (binary content).
  - For `write` and `edit` of a note (`.md`): the new file content.
  - For `write` of an attachment: empty string `""` (binary content cannot be passed as a string).
- `operation`: the operation that triggered the callback.

**Contract**: the callback fires **outside** the write lock. It must not raise;
unhandled exceptions are logged at `ERROR` but not propagated to the caller.

Default: `None` (no callback). Built-in option: `GitWriteStrategy` (or the
legacy factory `git_write_strategy(token=...)`) that auto-commits and pushes.

**Deprecation note**: `git_write_strategy()` factory function is preserved for
backward compatibility. Prefer constructing `GitWriteStrategy` directly for
access to `flush()` and `close()` methods.

### `scanner.py` -- File Discovery and Parsing

```python
def scan_directory(
    source_dir: Path,
    *,
    glob_pattern: str = "**/*.md",
    exclude_patterns: list[str] | None = None,
    required_frontmatter: list[str] | None = None,
) -> Iterator[ParsedNote]: ...

def parse_note(path: Path, source_dir: Path) -> ParsedNote: ...
```

**Frontmatter parsing**: use `python-frontmatter` library. Schema-agnostic.
Documents without frontmatter get an empty dict and proceed normally.

**Exclude patterns**: glob patterns (e.g., `[".obsidian/**", "_templates/**"]`)
matched against relative paths from `source_dir` using `pathlib.Path.match()`.

**Fault tolerance**: documents that cannot be decoded as UTF-8 are skipped with
a `WARNING` log message. `scan_directory()` is fault-tolerant; a single bad
file does not abort the scan.

### `fts_index.py` -- SQLite FTS5

```python
class FTSIndex:
    def __init__(self, db_path: Path | str = ":memory:"): ...
    def build_from_notes(self, notes: Iterable[ParsedNote]) -> int: ...
    def upsert_note(self, note: ParsedNote) -> int: ...
    def delete_by_path(self, path: str) -> int: ...
    def search(self, query: str, *, limit: int = 10,
               folder: str | None = None,
               filters: dict[str, str] | None = None) -> list[FTSResult]: ...
    def get_note(self, path: str) -> dict | None: ...
    def list_notes(self, *, folder: str | None = None) -> list[dict]: ...
    def list_folders(self) -> list[str]: ...
    def list_field_values(self, field: str) -> list[str]: ...
    def close(self) -> None: ...
```

Uses the schema defined in [Database Schema](#database-schema). Note that
`FTSIndex.search()` returns `list[FTSResult]` (raw BM25 results), while
`Collection.search()` returns `list[SearchResult]` (unified results with RRF
scoring in hybrid mode).

### `vector_index.py` -- Numpy Embeddings

Adapted from ifcraftcorpus `embeddings.py`. Rename `EmbeddingIndex` to
`VectorIndex`. The `load()` classmethod **must** import from
`markdown_vault_mcp.providers`, not `ifcraftcorpus.providers`.

The `VectorIndex` maintains a sidecar metadata list where each entry maps a
row index to `{path, title, folder, heading, content}`. This enables:
- Bulk deletion by document path (for reindex)
- Returning rich metadata with semantic search results

### `providers.py` -- Embedding Providers

Copied from ifcraftcorpus, adapted:
- Rename env var prefix `IFCRAFTCORPUS_` to `MARKDOWN_VAULT_MCP_`
- Fix any hardcoded package imports
- Keep the same provider ABC and implementations (Ollama, OpenAI,
  SentenceTransformers)

### `tracker.py` -- Change Detection

```python
class ChangeTracker:
    def __init__(self, state_path: Path): ...
    def detect_changes(self, source_dir: Path,
                       glob_pattern: str = "**/*.md") -> ChangeSet: ...
    def update_state(self, notes: list[ParsedNote]) -> None: ...
    def reset(self) -> None: ...
```

`tracker.py` is entirely new code (no ifcraftcorpus equivalent). State file
format: `{"Journal/note.md": "sha256hex", ...}` as JSON.

### `mcp_server.py` -- Generic MCP Server

Uses **FastMCP 3.0+** with lifespan hooks for Collection init/teardown.

**Tool surface** mirrors LLM file tool semantics (Claude Code Read/Write/Edit
pattern). Each tool is annotated with MCP `ToolAnnotations`:

| Tool | Description | `readOnlyHint` | `destructiveHint` | `idempotentHint` |
|------|-------------|:-:|:-:|:-:|
| `search` | Search the collection by query | `True` | `False` | `True` |
| `read` | Read a document's full content | `True` | `False` | `True` |
| `list_documents` | List documents, optionally filtered | `True` | `False` | `True` |
| `write` | Create or overwrite a document | `False` | `False` | `True` |
| `edit` | Patch a section (read-before-edit) | `False` | `False` | `False` |
| `rename` | Rename/move a document (Phase 2-3) | `False` | `False` | `False` |
| `delete` | Delete a document | `False` | **`True`** | `True` |
| `list_folders` | List all folders | `True` | `False` | `True` |
| `list_tags` | List tag values for a field | `True` | `False` | `True` |
| `stats` | Collection statistics | `True` | `False` | `True` |
| `reindex` | Incremental reindex | `False` | `False` | `True` |
| `build_embeddings` | Build/rebuild vector embeddings | `False` | `False` | `True` |
| `embeddings_status` | Check embedding provider status | `True` | `False` | `True` |

**Tool name note**: the MCP tool is registered as `list_documents` (not `list`)
to avoid shadowing Python's built-in `list`. The underlying `Collection.list()`
method is unchanged.

**Tag-based visibility**: `write`, `edit`, `delete`, `rename` are always
registered but tagged with ``tags={"write"}``. When ``read_only=True``, the
server calls ``mcp.disable(tags={"write"})`` to hide them from clients.
This also hides any prompts sharing the ``write`` tag (e.g. ``research``,
``discuss``, ``create_from_template``). The Collection still raises ``ReadOnlyError`` as a defence-in-depth
guard if a write method is somehow called on a read-only instance.

**Dynamic instructions**: the server's MCP `instructions` string varies with
`read_only` mode. When `read_only=True`, the instructions state this is a
read-only instance; when `read_only=False`, they describe write tool semantics.
This signals capability status to clients and reduces irrelevant prompting.

**Tool semantics**:
- `read(path)` returns full file content + frontmatter metadata
- `write(path, content, frontmatter?)` creates or overwrites entire file
- `edit(path, old_text, new_text)` reads file, verifies `old_text` exists
  exactly once, replaces, writes back. Fails on not-found or ambiguous match.
- `delete(path)` removes file, updates index, triggers `on_write`

These semantics are intentionally close to Claude Code's file tools for
familiarity. LLMs that know how to read/write/edit files can use these tools
without special prompting.

**Dependency injection**: tools and resources use FastMCP's
``Depends(get_collection)`` to access the Collection instance from
lifespan context, eliminating module-level globals. Prompts are pure
template functions with no collection dependency.

**Resources**: the server exposes 6 read-only MCP resources:

| URI | Source | Description |
|-----|--------|-------------|
| ``config://vault`` | ``CollectionConfig`` | Source dir, read-only flag, indexed fields, extensions |
| ``stats://vault`` | ``Collection.stats()`` | Document/chunk/folder counts, capabilities |
| ``tags://vault`` | ``Collection.list_tags()`` | All tags grouped by indexed field |
| ``tags://vault/{field}`` | ``Collection.list_tags(field)`` | Flat list for one field (template) |
| ``folders://vault`` | ``Collection.list_folders()`` | Sorted folder path list |
| ``toc://vault/{path}`` | ``Collection.get_toc(path)`` | Document headings with synthetic H1 title |

Resources return JSON (``mime_type="application/json"``). The ToC resource
queries the existing ``sections`` table — no file I/O.

**Prompts**: 6 built-in prompt templates:

| Prompt | Parameters | Tags | Description |
|--------|-----------|------|-------------|
| ``summarize`` | ``path`` | — | Summarize a document |
| ``research`` | ``topic`` | ``write`` | Search and consolidate as new note |
| ``discuss`` | ``path`` | ``write`` | Analyze and suggest improvements |
| ``create_from_template`` | ``template_name`` (optional) | ``write`` | Discover/read/fill/write from a template |
| ``related`` | ``path`` | — | Find related notes, suggest cross-references |
| ``compare`` | ``path1, path2`` | — | Compare two documents |

Write-tagged prompts are hidden in read-only mode by the same
``mcp.disable(tags={"write"})`` call that hides write tools.

## Configuration

### Phase 1: Python API Only

Configuration is the `Collection` constructor. No config files.

### Phase 2: Environment Variables

For MCP server deployment:

| Variable | Description | Default |
|----------|-------------|---------|
| `MARKDOWN_VAULT_MCP_SERVER_NAME` | MCP server name shown to clients | `markdown-vault-mcp` |
| `MARKDOWN_VAULT_MCP_INSTRUCTIONS` | System-level instructions for LLM context | generic description |
| `MARKDOWN_VAULT_MCP_SOURCE_DIR` | Path to markdown files | required |
| `MARKDOWN_VAULT_MCP_READ_ONLY` | Disable write tools | `true` |
| `MARKDOWN_VAULT_MCP_INDEX_PATH` | SQLite index path | in-memory |
| `MARKDOWN_VAULT_MCP_EMBEDDINGS_PATH` | Embeddings directory | disabled |
| `MARKDOWN_VAULT_MCP_INDEXED_FIELDS` | Comma-separated frontmatter fields to index | none |
| `MARKDOWN_VAULT_MCP_REQUIRED_FIELDS` | Comma-separated required frontmatter fields | none |
| `MARKDOWN_VAULT_MCP_EXCLUDE` | Comma-separated glob patterns to exclude | none |
| `MARKDOWN_VAULT_MCP_TEMPLATES_FOLDER` | Relative folder path for note templates used by `create_from_template` | `_templates` |
| `MARKDOWN_VAULT_MCP_GIT_REPO_URL` | HTTPS URL for managed git mode (clone + remote validation) | disabled |
| `MARKDOWN_VAULT_MCP_GIT_USERNAME` | Username for HTTPS token auth prompts | `x-access-token` |
| `MARKDOWN_VAULT_MCP_GIT_TOKEN` | Token/password for HTTPS git auth | disabled |
| `MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S` | Seconds between ff-only pull ticks | `600` |
| `MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S` | Seconds of idle before git push (0 = push on shutdown only) | `30` |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_NAME` | Committer name for auto-commits | `markdown-vault-mcp` |
| `MARKDOWN_VAULT_MCP_GIT_COMMIT_EMAIL` | Committer email for auto-commits | `noreply@markdown-vault-mcp` |
| `MARKDOWN_VAULT_MCP_GIT_LFS` | Run `git lfs pull` on startup to resolve LFS pointer files | `true` |
| `MARKDOWN_VAULT_MCP_BEARER_TOKEN` | Static bearer token for simple auth — clients send `Authorization: Bearer <token>` | none |
| `MARKDOWN_VAULT_MCP_BASE_URL` | Server's public URL, required to enable OIDC auth (e.g. `https://mcp.example.com`) | none |
| `MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL` | OIDC discovery URL (`/.well-known/openid-configuration`) | none |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID` | OIDC client ID registered with the provider | none |
| `MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET` | OIDC client secret | none |
| `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` | Persistent JWT signing key — **required for Docker/Linux** to survive restarts | ephemeral on Linux |
| `MARKDOWN_VAULT_MCP_OIDC_AUDIENCE` | JWT audience claim (required by some providers) | none |
| `MARKDOWN_VAULT_MCP_OIDC_REQUIRED_SCOPES` | Comma-separated OAuth scopes to request | `openid` |
| `MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN` | Verify the upstream access token as JWT instead of the id token. Set `true` only when your provider issues JWT access tokens and you need audience-claim validation on that token | `false` (verify id token) |
| `MARKDOWN_VAULT_MCP_ATTACHMENT_EXTENSIONS` | Comma-separated allowlist of non-.md extensions (without dot), e.g. `pdf,png,docx`; use `*` to allow all non-.md files | common list (pdf, png, jpg, docx, …) |
| `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB` | Maximum attachment size in MB enforced on both read and write; `0` disables the limit | `10.0` |
| `EMBEDDING_PROVIDER` | `openai`, `ollama`, `fastembed` | auto-detect |
| `OLLAMA_HOST` | Ollama server URL | `http://localhost:11434` |
| `MARKDOWN_VAULT_MCP_OLLAMA_MODEL` | Ollama embedding model | `nomic-embed-text` |
| `MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY` | Force CPU-only inference | `false` |
| `MARKDOWN_VAULT_MCP_FASTEMBED_MODEL` | FastEmbed model | `nomic-ai/nomic-embed-text-v1.5` |
| `MARKDOWN_VAULT_MCP_FASTEMBED_CACHE_DIR` | FastEmbed model cache directory | FastEmbed default |
| `OPENAI_API_KEY` | OpenAI API key | none |

#### Example Configurations

**Obsidian vault (read-only)**:
```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/home/user/Obsidian
MARKDOWN_VAULT_MCP_READ_ONLY=true
MARKDOWN_VAULT_MCP_EXCLUDE=.obsidian/**,.trash/**
EMBEDDING_PROVIDER=ollama
```

**IF Craft Corpus (strict frontmatter)**:
```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/data/corpus
MARKDOWN_VAULT_MCP_READ_ONLY=true
MARKDOWN_VAULT_MCP_REQUIRED_FIELDS=title,cluster
MARKDOWN_VAULT_MCP_INDEXED_FIELDS=cluster,topics
```

**Obsidian vault (read-write, git-backed)**:
```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/data/vault
MARKDOWN_VAULT_MCP_READ_ONLY=false
MARKDOWN_VAULT_MCP_EXCLUDE=.obsidian/**,.trash/**,_templates/**
MARKDOWN_VAULT_MCP_GIT_REPO_URL=https://github.com/acme/vault.git
MARKDOWN_VAULT_MCP_GIT_USERNAME=x-access-token
MARKDOWN_VAULT_MCP_GIT_TOKEN=ghp_xxx
```

**Bearer token auth (simple)**:
```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/data/vault
MARKDOWN_VAULT_MCP_BEARER_TOKEN=your-secret-token
```

**Obsidian vault with OIDC auth (Authelia)**:
```bash
MARKDOWN_VAULT_MCP_SOURCE_DIR=/data/vault
MARKDOWN_VAULT_MCP_READ_ONLY=true
MARKDOWN_VAULT_MCP_BASE_URL=https://mcp.example.com
MARKDOWN_VAULT_MCP_OIDC_CONFIG_URL=https://auth.example.com/.well-known/openid-configuration
MARKDOWN_VAULT_MCP_OIDC_CLIENT_ID=markdown-vault-mcp
MARKDOWN_VAULT_MCP_OIDC_CLIENT_SECRET=your-client-secret
MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY=your-random-secret   # required on Linux/Docker
```

#### Authentication

The server supports three auth modes, resolved in order of precedence:

1. **Bearer token** — simple static token via `MARKDOWN_VAULT_MCP_BEARER_TOKEN`
2. **OIDC** — full OAuth 2.1 flow via `OIDCProxy` (requires `BASE_URL`, `OIDC_CONFIG_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`)
3. **No auth** — server accepts all connections (default)

The first configured mode wins. If both bearer token and OIDC are configured, bearer token takes precedence and a warning is logged.

#### Bearer Token Authentication

Set `MARKDOWN_VAULT_MCP_BEARER_TOKEN` to a secret string. Clients must send an `Authorization: Bearer <token>` header with every request. Uses FastMCP's `StaticTokenVerifier` — no external dependencies or identity providers needed.

Best for deployments behind a VPN, in a Docker compose stack, or on a private network where full OIDC is unnecessary.

#### OIDC Authentication

When all four required vars are set (`BASE_URL`, `OIDC_CONFIG_URL`, `OIDC_CLIENT_ID`, `OIDC_CLIENT_SECRET`), the server uses FastMCP's `OIDCProxy` to authenticate MCP clients via OAuth 2.1 + PKCE. The server auto-discovers provider endpoints from the OIDC discovery URL so no additional endpoint configuration is needed.

**Token verification:** By default the server verifies the upstream `id_token` (always a standard JWT per OIDC Core) rather than the `access_token`. This works with all providers, including those that issue opaque (non-JWT) access tokens (e.g. Authelia). Set `MARKDOWN_VAULT_MCP_OIDC_VERIFY_ACCESS_TOKEN=true` to revert to access-token JWT verification when audience-claim validation on that token is required.

**Token lifetime recommendations:** MCP clients do not reliably re-authenticate after token expiry. Configure long token lifetimes on your identity provider (e.g. 8h access tokens, 30d refresh tokens) and include `offline_access` in the provider-side scopes. See the [authentication guide](guides/authentication.md#session-drops-after-token-expiry) for details and upstream issue links.

**Authelia client registration** (in your Authelia `configuration.yml`):
```yaml
identity_providers:
  oidc:
    lifespans:
      custom:
        mcp_long_lived:
          access_token: '8h'
          refresh_token: '30d'
    clients:
      - client_id: markdown-vault-mcp
        client_secret: '$pbkdf2-sha512$...'   # hashed secret
        lifespan: 'mcp_long_lived'
        redirect_uris:
          - https://mcp.example.com/auth/callback
        grant_types:
          - authorization_code
          - refresh_token
        response_types:
          - code
        pkce_challenge_method: S256
        scopes:
          - openid
          - profile
          - email
          - offline_access
        token_endpoint_auth_method: client_secret_post
```

**Linux/Docker note:** FastMCP uses an ephemeral JWT signing key on Linux by default — every restart invalidates all client tokens and forces re-authentication. Set `MARKDOWN_VAULT_MCP_OIDC_JWT_SIGNING_KEY` to a stable random secret (e.g. `openssl rand -hex 32`) to persist tokens across restarts.

#### Attachment Support

The server supports reading and writing non-markdown binary files (PDFs, images, etc.) by overloading the existing MCP tools — no new tool registrations.

**Extension-based dispatch**: `.md` files always follow the markdown path. All other extensions are treated as attachments if they appear in the configured allowlist.

**Default allowlist**: `pdf png jpg jpeg gif webp svg bmp tiff docx xlsx pptx odt ods odp zip tar gz mp3 mp4 wav ogg txt csv tsv json yaml toml xml html css js ts`. The `.md` extension is always excluded regardless of configuration.

**Tool behaviour changes**:

| Tool | .md path | Attachment path |
|------|----------|-----------------|
| `read(path)` | returns markdown body + frontmatter | returns `{path, mime_type, size_bytes, content_base64, modified_at}` |
| `write(path, content, frontmatter, content_base64)` | uses `content` + `frontmatter` | uses `content_base64` (base64-encoded bytes) |
| `list_documents(include_attachments=False)` | notes only (unchanged) | also returns `AttachmentInfo` entries with `kind="attachment"`, `mime_type` |
| `delete(path)` | removes file + index entries | removes file only (no index) |
| `rename(old, new)` | moves file + updates index | moves file only (no index) |
| `stats()` | includes `attachment_extensions` list | — |

Attachments are **not indexed or searched** — only direct path-based read/write/delete/rename. MIME type is detected via Python's `mimetypes.guess_type()` (no extra dependencies).

Size limit applies to both `read_attachment()` and `write_attachment()`. Set `MARKDOWN_VAULT_MCP_MAX_ATTACHMENT_SIZE_MB=0` to disable the limit.

### Phase 3: Evaluate YAML

If multi-collection or complex per-collection settings are needed, add YAML
config using `pydantic-settings` for type validation and env var overlay.
Evaluate at deploy time, not before.

## Packaging

```toml
[project]
name = "markdown-vault-mcp"
requires-python = ">=3.10"
dependencies = [
    "python-frontmatter>=1.0",
]

[project.optional-dependencies]
mcp = ["fastmcp>=3.0,<4"]
embeddings-api = ["httpx>=0.25", "numpy>=1.20"]
embeddings = ["fastembed>=0.3", "numpy>=1.20"]
all = ["fastmcp>=3.0,<4", "httpx>=0.25", "fastembed>=0.3", "numpy>=1.20"]
dev = ["pytest>=7.0", "pytest-cov>=4.0", "ruff>=0.1", "mypy>=1.0"]

[project.scripts]
markdown-vault-mcp = "markdown_vault_mcp.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

Note: `pyyaml` is not listed as a direct dependency; it is a transitive
dependency of `python-frontmatter`.

## Deployment

### Docker

Same pattern as ifcraftcorpus: `python:3.12-slim` base, `uv` for installs.
CI/CD, GitHub Actions, and PyPI publishing adapted from ifcraftcorpus with
minimal changes.

**Volume layout**: two volumes — `/data/vault` (user content, bind-mount or
named volume) and `/data/state` (all internal state: SQLite index, embeddings,
FastEmbed model cache, OIDC proxy state via `FASTMCP_HOME`). The Dockerfile
sets `FASTMCP_HOME=/data/state/fastmcp` so OIDCProxy persists JTI mappings
and upstream tokens across restarts.

**Entrypoint + gosu privilege drop**: the container starts as root and runs
`docker-entrypoint.sh`, which chowns `/data/*` to `appuser` (fixing
root-owned named volumes), then drops to non-root via `gosu` before
executing the application. Runtime UID/GID override via `PUID`/`PGID`
env vars (default 1000/1000). This is the same pattern used by official
PostgreSQL, Redis, and MySQL images.

Deployed behind **litellm MCP gateway + mcp-auth-proxy** (same as
ifcraftcorpus).

### Write + Git Integration

Three git modes:

1. **Managed mode** (`GIT_REPO_URL` set): server owns git lifecycle.
   Startup clones into `SOURCE_DIR` if empty, or verifies existing `origin`
   matches the configured repo URL. Pull loop + commit + deferred push enabled.
2. **Unmanaged / commit-only mode** (no `GIT_REPO_URL`): server commits local writes if
   `SOURCE_DIR` is already a git repo, but never pulls or pushes.
3. **No-git mode**: when `SOURCE_DIR` is not a git repo, git callbacks no-op.

Backward compatibility: `GIT_TOKEN` without `GIT_REPO_URL` keeps previous
pull+push behavior against the existing checkout but logs a deprecation warning.

In managed/legacy push-enabled modes, `GitWriteStrategy` commits per-write and
defers push to a background timer (`MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S`,
default 30s). After the idle period elapses with no writes, all accumulated
local commits are pushed in a single `git push`. On shutdown,
`Collection.close()` flushes any pending push.

Startup recovery: `GitWriteStrategy` checks for unpushed local commits
(`git log @{upstream}..HEAD`) on first invocation and pushes them before
accepting new writes.

**Deferred push mechanics**: `GitWriteStrategy` uses a `threading.Timer` that
resets on each write. After `push_delay_s` seconds of idle, all accumulated
local commits are pushed in a single `git push`. `flush()` cancels the timer
and pushes synchronously. `close()` calls `flush()` and marks the strategy as
closed (subsequent writes are ignored).

For private repos, HTTPS token auth uses:

- `MARKDOWN_VAULT_MCP_GIT_USERNAME` (default `x-access-token`)
- `MARKDOWN_VAULT_MCP_GIT_TOKEN`

Provider-specific usernames:

- GitHub: `x-access-token`
- GitLab: `oauth2`
- Bitbucket: account username

**Git credential security**: when a token is supplied, `GitWriteStrategy` uses
a `GIT_ASKPASS` temporary script rather than embedding the token in any
command-line argument. This means the token is never visible in
`/proc/<pid>/cmdline` or process listings. The script is created with `0o700`
permissions (owner-only) and deleted in a `finally` block regardless of push
outcome. Tokens are also **redacted** from all error log messages (replaced
with `***`).

**Git LFS support**: when `MARKDOWN_VAULT_MCP_GIT_LFS=true` (default),
`GitWriteStrategy` calls `_lfs_pull()` once during lazy initialisation — after
startup recovery (`_push_if_unpushed()`), outside the init lock — to resolve
LFS pointer files before the first write is committed. `_lfs_pull()` runs
`git lfs pull` in the vault root; failures (including `git lfs` not installed
or any non-zero exit) are logged at ERROR and never propagated to the caller.
Set `MARKDOWN_VAULT_MCP_GIT_LFS=false` for repos that do not use LFS, or when
`git-lfs` is not available on PATH.

**Periodic pull (ff-only)**: in push-enabled managed/legacy modes, when
`MARKDOWN_VAULT_MCP_GIT_PULL_INTERVAL_S > 0` (default `600`), the server:

- Runs one `git fetch` + ff-only update **before** the initial `build_index()`
  so the index scans the freshest working tree.
- Starts a daemon thread that repeats `fetch + ff-only update` every interval.
- After a successful fast-forward that advanced `HEAD`, triggers
  `Collection.reindex()` to incrementally update the index.
- Blocks write operations during the **reindex phase** of each pull tick
  (not during fetch/ff-only merge) by acquiring the Collection write lock.
  Read/search operations are not blocked at the Python level (SQLite WAL
  enables concurrent readers during index writes).
- If `MARKDOWN_VAULT_MCP_GIT_LFS=true`, each successful pull tick ends with
  `git lfs pull` so LFS pointer files are resolved before reads and indexing.

Safety branch mode for push failures is tracked separately (see #119).

### Future Work

- **FastMCP OAuth**: implemented via `OIDCProxy` — see OIDC Authentication section above.

## Implementation Plan

### Phase 1: Core Library + API Validation

**API surface**: `Collection.__init__`, `search`, `read`, `list`,
`build_index`, `reindex`, `build_embeddings`, `embeddings_status`,
`list_folders`, `list_tags`, `stats`.

1. Create repo structure, packaging, CI/CD (adapted from ifcraftcorpus)
2. Copy + adapt `providers.py` and `embeddings.py` (rename to `vector_index.py`)
3. Implement `scanner.py` -- frontmatter parsing, heading-based chunking,
   `ChunkStrategy` protocol
4. Implement `fts_index.py` -- generic FTS5 with `document_tags`, RRF hybrid
5. Implement `tracker.py` -- hash-based change detection
6. Implement `collection.py` -- thin facade tying it all together
7. Tests for all modules (fixtures with sample .md files covering: no
   frontmatter, partial frontmatter, malformed YAML, deep headings, unicode,
   invalid UTF-8)
8. **Validate API**: configure `Collection` with ifcraftcorpus settings
   (`required_frontmatter=["title", "cluster"]`,
   `indexed_frontmatter_fields=["cluster", "topics"]`). Build index, run
   search, verify tag filtering works. If the API doesn't accommodate the
   corpus use case, fix it before Phase 2.

### Phase 2: MCP Server + CI/CD

**API surface adds**: MCP tools, CLI.

9. Implement `mcp_server.py` with all read-only tools, `ToolAnnotations`,
   lifespan hooks
10. Implement `cli.py` -- `serve`, `index`, `search`, `reindex` commands
11. Configuration loading (env vars)
12. Docker + GitHub Actions + PyPI (adapted from ifcraftcorpus)
13. Validate against Obsidian vault (`pvliesdonk/obsidian.md`) as read-only
    collection
14. MCP tool integration tests using FastMCP test client

### Phase 3: Deploy + Write Support

**API surface adds**: `write`, `edit`, `delete`, `rename`.

15. Deploy to homelab (Traefik + mcp-auth-proxy)
16. Write support: `write`, `edit`, `delete` tools
17. `on_write` callback with git strategy
18. `rename` tool
19. Test with Obsidian vault in read-write mode
20. Evaluate YAML config need

### Phase 4: Publish + ifcraftcorpus Refactor

21. Publish markdown-vault-mcp 1.0 to PyPI
22. Refactor ifcraftcorpus to depend on markdown-vault-mcp
23. ifcraftcorpus becomes thin wrapper + domain tools + subagent prompts

## Testing Strategy

- **Fixtures**: `tests/fixtures/` directory with sample vault documents in
  several shapes: no frontmatter, minimal frontmatter, full frontmatter,
  malformed YAML, deeply nested headings, unicode, empty files, invalid UTF-8.
- **Unit tests**: scanner (frontmatter parsing, chunking, required_frontmatter
  filtering, UTF-8 fault tolerance), FTS index (CRUD, search, tag filtering,
  RRF hybrid), change tracker (detect changes, update state), vector index
  (add, search, save/load, metadata consistency).
- **Integration tests**: Collection end-to-end (scan -> index -> search ->
  reindex), write + reindex roundtrip (write makes content searchable),
  MCP server tool invocations via FastMCP test client.
- **Regression tests**: hybrid score ordering (search for a query that matches
  in both FTS5 and semantic; verify RRF merges ranks so neither signal
  dominates), document identity (same filename in different folders produces
  distinct results), frontmatter-less documents indexed correctly.
- **API validation**: Phase 1 includes a test that configures `Collection`
  with ifcraftcorpus settings and verifies search + tag filtering work.
- **Coverage**: enforce with `coverage.py` `fail_under` (same pattern as
  ifcraftcorpus).

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| VRAM contention (Ollama on RTX 4060 8GB) | `cpu_only` mode, batch embeddings |
| Vault scale (numpy in-memory) | Fine for thousands of documents. If tens of thousands, evaluate Qdrant. |
| Concurrent writes (Obsidian + MCP) | Use git as sync layer. MCP server should not write directly to live Obsidian vault without git in between. |
| FastMCP breaking changes | Pin `>=3.0,<4`. Monitor for 4.0 migration. |
| `Collection` API doesn't fit ifcraftcorpus | Validate in Phase 1 before building MCP server. |

## Appendix: Decision Log

Decisions made during design review (2026-03-07):

| # | Topic | Decision | Rationale |
|---|-------|----------|-----------|
| 1 | Document identity | Relative path with `.md` extension | Avoids collisions between same-name files in different folders |
| 2 | Frontmatter handling | Optional by default; `required_frontmatter` config | Obsidian vaults rarely have frontmatter; ifcraftcorpus requires it |
| 3 | Hybrid scoring | Reciprocal Rank Fusion (RRF) | Fixes latent bug: raw BM25 vs cosine comparison |
| 4 | Phasing | Validate API against ifcraftcorpus in Phase 1 | Discover API mismatches before shipping |
| 5 | Code reuse | Copy + adapt (not move) | ifcraftcorpus stays as-is; temporary duplication accepted |
| 6 | Module structure | Thin facade + specialized modules | Avoid fat modules; prefer focused abstractions |
| 7 | Frontmatter filtering | `document_tags` table (indexed) + raw JSON blob | Performant multi-value filtering without `json_extract()` |
| 8 | Configuration | Phase 1: API. Phase 2: env vars. Phase 3: evaluate YAML | Avoid premature config complexity |
| 9 | Chunking | `heading` + `whole` with `ChunkStrategy` protocol | Extensible without over-engineering; `sliding` deferred |
| 10 | FastMCP | Pin `>=3.0,<4`; lifespan hooks; follow conventions | Proper init/teardown; forward-compatible |
| 11 | Write support | Separate frontmatter param; generic `on_write` callback | Git strategy as built-in; extensible for future strategies |
| 12 | Docker/CI | Bring early (Phase 2); adapt from ifcraftcorpus | Proven infrastructure, minimal changes needed |
| 13.1 | Error handling | Library raises; MCP catches and returns structured | Clean separation of concerns |
| 13.2 | Logging | Follow FastMCP conventions; `logging.getLogger(__name__)` | Standardized, no `print()` |
| 13.3 | Concurrency | Library sync; `asyncio.to_thread()` in MCP layer | Appropriate for single-user; async provider as future work |
| 13.4 | FTS5 schema | `path`, `title`, `folder`, `heading`, `content` | Generic; domain filtering via `document_tags` |
| 13.5 | File extension | Include `.md` in document identifier | Unambiguous, matches filesystem |
| 14 | Python library use | Document as use case; `Collection` is primary API | MCP is one consumer; LangChain wrapper is downstream |
| 15 | Rename | Include in design, defer to Phase 2-3 | Touches every layer; not critical for initial release |
| 16 | Tool semantics | Mirror Claude Code Read/Write/Edit; MCP `ToolAnnotations` | Familiar to LLMs; `delete` marked destructive |
