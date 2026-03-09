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
| `DocumentNotFoundError` | `read()`, `edit()`, `delete()`, `rename()` | Document path does not exist on disk |
| `ReadOnlyError` | `write()`, `edit()`, `delete()`, `rename()` | `read_only=True` |
| `EditConflictError` | `edit()` | `old_text` not found or appears more than once |
| `DocumentExistsError` | `rename()` | `new_path` already exists |
| `ValueError` | `build_embeddings()` | No `embedding_provider` or `embeddings_path` configured |

If a provider fails mid-`build_embeddings()`, the partial state is NOT saved;
the previous embeddings file (if any) is left intact.

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
`logging.getLogger(__name__)` throughout. Include a `configure_logging()` setup
module (adapted from ifcraftcorpus). No `print()` for operational output.

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

@dataclass
class NoteInfo:
    """Summary info for a document, returned by list()."""
    path: str
    title: str
    folder: str
    frontmatter: dict[str, Any]
    modified_at: float

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
              frontmatter: dict | None = None) -> WriteResult: ...
    def edit(self, path: str, old_text: str,
             new_text: str) -> EditResult: ...
    def delete(self, path: str) -> DeleteResult: ...
    def rename(self, old_path: str, new_path: str) -> RenameResult: ...
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

- `path`: absolute path on disk (for `delete`, the path before deletion; for
  `rename`, the new path).
- `content`: new file content (empty string `""` for `delete`).
- `operation`: which operation triggered the callback.

Default: `None` (no callback). Built-in option: git strategy factory
`git_write_strategy(token=...)` that auto-commits and pushes.

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
| `list` | List documents, optionally filtered | `True` | `False` | `True` |
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

**Conditional registration**: `write`, `edit`, `delete`, `rename` are only
registered when `read_only=False`. If a client somehow invokes them on a
read-only server, `ReadOnlyError` is raised (converted to structured error
response by the MCP layer).

**Tool semantics**:
- `read(path)` returns full file content + frontmatter metadata
- `write(path, content, frontmatter?)` creates or overwrites entire file
- `edit(path, old_text, new_text)` reads file, verifies `old_text` exists
  exactly once, replaces, writes back. Fails on not-found or ambiguous match.
- `delete(path)` removes file, updates index, triggers `on_write`

These semantics are intentionally close to Claude Code's file tools for
familiarity. LLMs that know how to read/write/edit files can use these tools
without special prompting.

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
| `MARKDOWN_VAULT_MCP_GIT_TOKEN` | PAT for git push on write | disabled |
| `MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S` | Seconds of idle before git push (0 = push on shutdown only) | `30` |
| `EMBEDDING_PROVIDER` | `ollama`, `openai`, `sentence-transformers` | auto-detect |
| `OLLAMA_HOST` | Ollama server URL | `http://localhost:11434` |
| `MARKDOWN_VAULT_MCP_OLLAMA_MODEL` | Ollama embedding model | `nomic-embed-text` |
| `MARKDOWN_VAULT_MCP_OLLAMA_CPU_ONLY` | Force CPU-only inference | `false` |
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
MARKDOWN_VAULT_MCP_GIT_TOKEN=ghp_xxx
```

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
embeddings = ["sentence-transformers>=2.0", "numpy>=1.20"]
all = ["fastmcp>=3.0,<4", "httpx>=0.25", "numpy>=1.20"]
all-local = ["fastmcp>=3.0,<4", "httpx>=0.25", "numpy>=1.20", "sentence-transformers>=2.0"]
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

Deployed behind **litellm MCP gateway + mcp-auth-proxy** (same as
ifcraftcorpus).

### Write + Git Integration

Three strategies supported via the `on_write` callback:

1. **No push (default)**: write to disk only. External process (cron, hook)
   handles `git add + commit + push`.
2. **Git strategy with deferred push**: `GitWriteStrategy` commits per-write
   and defers push to a background timer (`MARKDOWN_VAULT_MCP_GIT_PUSH_DELAY_S`,
   default 30s). After the idle period elapses with no writes, all accumulated
   local commits are pushed in a single `git push`. On shutdown,
   `Collection.close()` flushes any pending push.
3. **Git strategy with push on shutdown**: set `GIT_PUSH_DELAY_S=0` — commits
   per-write, push only on `Collection.close()` (MCP lifespan teardown).

Startup recovery: `GitWriteStrategy` checks for unpushed local commits
(`git log @{upstream}..HEAD`) on first invocation and pushes them before
accepting new writes.

For private repos (like `pvliesdonk/obsidian.md`), the git strategy needs
credentials. Options: SSH key mount or PAT via env var.

### Future Work

- **FastMCP OAuth**: evaluate built-in OAuth support as alternative to
  mcp-auth-proxy for public-facing deployments.

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
