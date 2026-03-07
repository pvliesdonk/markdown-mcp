# markdown-mcp: Design Specification v2

> Generic markdown collection MCP server with FTS5 + semantic search,
> frontmatter-aware indexing, and incremental reindexing. Extracted from
> and replacing the search layer in `pvliesdonk/if-craft-corpus`.

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
markdown-mcp (new package)
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
+-- depends on markdown-mcp
+-- ships corpus/ content
+-- adds domain-specific tools (search_exemplars, list_exemplar_tags)
+-- adds subagent prompts
+-- thin wrapper: configures Collection with required_frontmatter
```

**ifcraftcorpus stays as-is** during markdown-mcp development. No changes to
the existing package until a complete refactor after markdown-mcp is stable.

## Reference Code

All code below lives in `pvliesdonk/if-craft-corpus`. Read these files for
implementation patterns:

| File | Reuse | Notes |
|------|-------|-------|
| `providers.py` | **Copy + adapt** | Rename env var prefix `IFCRAFTCORPUS_` to `MARKDOWN_MCP_`. Fix hardcoded imports. |
| `embeddings.py` | **Copy + adapt** | Rename to `vector_index.py`. Fix `load()` import path. |
| `search.py` | **Adapt** | Pattern for `Collection` facade. Replace domain methods with generic API. |
| `index.py` | **Adapt** | Pattern for `fts_index.py`. Replace corpus-specific schema. Fix hybrid score bug. |
| `parser.py` | **Replace** | Replace with generic frontmatter + heading-based chunking. |
| `mcp_server.py` | **Adapt** | Replace domain tools with generic tools. Use lifespan hooks. |
| `cli.py` | **Adapt** | Simplify for markdown-mcp. |

Temporary code duplication is accepted. It resolves when ifcraftcorpus is
eventually refactored to depend on markdown-mcp.

## Core Design Decisions

### Document Identity

Documents are identified by their **relative path from the collection root**,
including the `.md` extension. Example: `Journal/2024-01-15.md`.

This avoids collisions between files with the same stem in different
directories (e.g., `Journal/2024-01-15.md` vs `Archive/2024-01-15.md`).

### Frontmatter Handling

Frontmatter is **optional by default**. Files without frontmatter are indexed
normally with an empty metadata dict. Title defaults to the first H1 heading,
then the filename.

A `required_frontmatter` configuration option enforces specific fields:

```python
Collection(
    source_dir=Path("corpus/"),
    required_frontmatter=["title", "cluster"],  # files missing these are skipped
)
```

- `None` (default): all `.md` files are indexed regardless of frontmatter.
- `["title", "cluster"]`: files missing any listed field are silently skipped.

This lets ifcraftcorpus enforce its schema while Obsidian vaults index
everything.

### Frontmatter Filtering

Hybrid approach:

1. **`document_tags` table** (indexed) for structured filtering. An
   `indexed_frontmatter_fields` config option specifies which frontmatter keys
   get promoted into `(tag_key, tag_value)` rows. Multi-valued fields (lists)
   produce one row per value.
2. **Raw frontmatter JSON blob** stored in the `documents` table for display
   and retrieval only -- not queried via index.

The `filters` parameter on `search()` generates
`document_id IN (SELECT ... FROM document_tags WHERE ...)` subqueries. This
gives O(1) indexed lookup without `json_extract()` performance problems.

### FTS5 Schema

Generic columns replacing the corpus-specific `cluster`/`topics`:

```sql
CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
    path,
    title,
    folder,
    heading,
    content,
    tokenize='porter unicode61'
);
```

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
class ChunkStrategy(Protocol):
    def chunk(self, content: str, metadata: dict) -> list[Chunk]: ...
```

**Phase 1 implementations**:
- `HeadingChunker`: split on H1/H2 boundaries. Short notes stay as single
  chunk. Each chunk inherits the note's frontmatter. Default.
- `WholeDocumentChunker`: one chunk per note. Good for short notes.

**Future** (deferred):
- `SlidingWindowChunker`: fixed-size overlapping windows with configurable
  tokenizer.

The `Collection` config accepts `chunk_strategy: str | ChunkStrategy` -- string
for built-in names, or pass a custom instance.

### Change Tracking

**Hash-based**, not git-based. Works with any directory, no git dependency.

- State file: `{relative_path: sha256_hash}` as JSON.
- On `reindex()`: scan all files, compare hashes to stored state, re-parse and
  re-embed only changed/added files, remove deleted entries.

**Trigger model**: startup scan + explicit `reindex` tool call. No background
polling in Phase 1. Architecture supports adding `watch_interval` or watchdog
integration later without refactoring.

### Incremental Reindex

Full numpy array rebuild on every reindex (filter unchanged rows + append new).
Only changed files are re-embedded (the expensive API call part). This is
correct and simple at vault scale (even 10k chunks at 768 dimensions is ~60MB).

Each embedding row stores the source file path in metadata, enabling
group-delete by file.

### Error Handling

Two-layer model:

- **Library layer** (`Collection`, `FTSIndex`, `VectorIndex`, etc.): raises
  specific exceptions (`DocumentNotFoundError`, `ReadOnlyError`,
  `IndexError`). Callers catch and handle.
- **MCP tool layer**: catches exceptions, returns structured error responses
  per FastMCP conventions.

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

## Module Design

### `collection.py` -- Thin Facade

The main interface. Orchestrates specialized modules. Target: ~200 lines.

```python
class Collection:
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
        on_write: Callable[[Path, str], None] | None = None,
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

    # --- Metadata ---
    def list_folders(self) -> list[str]: ...
    def list_tags(self, field: str = "tags") -> list[str]: ...
    def stats(self) -> CollectionStats: ...
```

**Write operations** (`write`, `edit`, `delete`, `rename`) raise
`ReadOnlyError` when `read_only=True`.

**`edit` behavior**: reads the file first, verifies `old_text` exists exactly
once in the file content, replaces it with `new_text`, writes back, updates
index, triggers `on_write` callback. Fails if `old_text` is not found or is
ambiguous (multiple matches).

**`on_write` callback**: generic interface invoked after any write operation.
Default: no-op. Built-in option: git strategy (auto-commit + push using
configured PAT via `GITHUB_TOKEN` or `MARKDOWN_MCP_GIT_TOKEN`). Extensible
for future strategies.

### `scanner.py` -- File Discovery and Parsing

```python
@dataclass
class ParsedNote:
    path: str                    # relative to source_dir, includes .md extension
    frontmatter: dict[str, Any]  # parsed YAML frontmatter (empty dict if none)
    title: str                   # from frontmatter, first H1, or filename
    chunks: list[Chunk]
    content_hash: str            # SHA256 of raw file content
    modified_at: float           # file mtime

@dataclass
class Chunk:
    heading: str | None          # heading text, None for preamble
    heading_level: int           # 0 for preamble, 1-6 for headings
    content: str
    start_line: int

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
Files without frontmatter get an empty dict and proceed normally.

### `fts_index.py` -- SQLite FTS5

```python
class FTSIndex:
    def __init__(self, db_path: Path | str = ":memory:"): ...
    def build_from_notes(self, notes: Iterable[ParsedNote]) -> int: ...
    def upsert_note(self, note: ParsedNote) -> int: ...
    def delete_by_path(self, path: str) -> int: ...
    def search(self, query: str, *, limit: int = 10,
               folder: str | None = None,
               filters: dict[str, str] | None = None) -> list[SearchResult]: ...
    def get_note(self, path: str) -> dict | None: ...
    def list_notes(self, *, folder: str | None = None) -> list[dict]: ...
    def list_folders(self) -> list[str]: ...
    def list_field_values(self, field: str) -> list[str]: ...
    def close(self) -> None: ...
```

Schema: `documents`, `sections`, `document_tags` (indexed), `notes_fts`
(FTS5 virtual table with `path`, `title`, `folder`, `heading`, `content`).

### `vector_index.py` -- Numpy Embeddings

Adapted from ifcraftcorpus `embeddings.py`. Rename `EmbeddingIndex` to
`VectorIndex`. Fix hardcoded import in `load()`.

### `providers.py` -- Embedding Providers

Copied from ifcraftcorpus, adapted:
- Rename env var prefix `IFCRAFTCORPUS_` to `MARKDOWN_MCP_`
- Fix any hardcoded package imports
- Keep the same provider ABC and implementations (Ollama, OpenAI,
  SentenceTransformers)

### `tracker.py` -- Change Detection

```python
@dataclass
class ChangeSet:
    added: list[str]
    modified: list[str]
    deleted: list[str]
    unchanged: int

class ChangeTracker:
    def __init__(self, state_path: Path): ...
    def detect_changes(self, source_dir: Path,
                       glob_pattern: str = "**/*.md") -> ChangeSet: ...
    def update_state(self, notes: list[ParsedNote]) -> None: ...
    def reset(self) -> None: ...
```

### `mcp_server.py` -- Generic MCP Server

Uses **FastMCP 2.5+** with lifespan hooks for Collection init/teardown.

**Tool surface** mirrors LLM file tool semantics (Claude Code Read/Write/Edit
pattern). Each tool is annotated with MCP `ToolAnnotations`:

| Tool | Description | `readOnlyHint` | `destructiveHint` | `idempotentHint` |
|------|-------------|:-:|:-:|:-:|
| `search` | Search the collection by query | `True` | `False` | `True` |
| `read` | Read a note's full content | `True` | `False` | `True` |
| `list` | List notes, optionally filtered by folder | `True` | `False` | `True` |
| `write` | Create or overwrite a note | `False` | `False` | `True` |
| `edit` | Patch a section of a note (read-before-edit) | `False` | `False` | `False` |
| `rename` | Rename/move a note (Phase 2-3) | `False` | `False` | `False` |
| `delete` | Delete a note | `False` | **`True`** | `True` |
| `list_folders` | List all folders in the collection | `True` | `False` | `True` |
| `list_tags` | List tag values for a frontmatter field | `True` | `False` | `True` |
| `stats` | Collection statistics | `True` | `False` | `True` |
| `reindex` | Incremental reindex (detect changes) | `False` | `False` | `True` |
| `build_embeddings` | Build/rebuild vector embeddings | `False` | `False` | `True` |
| `embeddings_status` | Check embedding provider status | `True` | `False` | `True` |

**Conditional registration**: `write`, `edit`, `delete`, `rename` are only
registered when `read_only=False`.

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
| `MARKDOWN_MCP_SOURCE_DIR` | Path to markdown files | required |
| `MARKDOWN_MCP_READ_ONLY` | Disable write tools | `true` |
| `MARKDOWN_MCP_INDEX_PATH` | SQLite index path | in-memory |
| `MARKDOWN_MCP_EMBEDDINGS_PATH` | Embeddings directory | disabled |
| `MARKDOWN_MCP_INDEXED_FIELDS` | Comma-separated frontmatter fields to index | none |
| `MARKDOWN_MCP_REQUIRED_FIELDS` | Comma-separated required frontmatter fields | none |
| `MARKDOWN_MCP_EXCLUDE` | Comma-separated glob patterns to exclude | none |
| `MARKDOWN_MCP_GIT_TOKEN` | PAT for git push on write | disabled |
| `EMBEDDING_PROVIDER` | `ollama`, `openai`, `sentence-transformers` | auto-detect |
| `OLLAMA_HOST` | Ollama server URL | `http://localhost:11434` |
| `MARKDOWN_MCP_OLLAMA_MODEL` | Ollama embedding model | `nomic-embed-text` |
| `MARKDOWN_MCP_OLLAMA_CPU_ONLY` | Force CPU-only inference | `false` |
| `OPENAI_API_KEY` | OpenAI API key | none |

### Phase 3: Evaluate YAML

If multi-collection or complex per-collection settings are needed, add YAML
config using `pydantic-settings` for type validation and env var overlay.
Evaluate at deploy time, not before.

## Packaging

```toml
[project]
name = "markdown-mcp"
requires-python = ">=3.10"
dependencies = [
    "pyyaml>=6.0",
    "python-frontmatter>=1.0",
]

[project.optional-dependencies]
mcp = ["fastmcp>=2.5,<3"]
embeddings-api = ["httpx>=0.25", "numpy>=1.20"]
embeddings = ["sentence-transformers>=2.0", "numpy>=1.20"]
all = ["fastmcp>=2.5,<3", "httpx>=0.25", "numpy>=1.20"]
dev = ["pytest>=7.0", "pytest-cov>=4.0", "ruff>=0.1", "mypy>=1.0"]

[project.scripts]
markdown-mcp = "markdown_mcp.cli:main"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

## Deployment

### Docker

Same pattern as ifcraftcorpus: `python:3.12-slim` base, `uv` for installs.
CI/CD, GitHub Actions, and PyPI publishing adapted from ifcraftcorpus with
minimal changes.

Deployed behind **litellm MCP gateway + mcp-auth-proxy** (same as
ifcraftcorpus).

### Write + Git Integration

Two strategies supported via the `on_write` callback:

1. **No push (default)**: write to disk only. External process (cron, hook)
   handles `git add + commit + push`.
2. **Git strategy**: auto-commit + push using `MARKDOWN_MCP_GIT_TOKEN` or
   `GITHUB_TOKEN`. Entire vault runs in-container.

For private repos (like `pvliesdonk/obsidian.md`), the git strategy needs
credentials. Options: SSH key mount or PAT via env var.

### Future Work

- **FastMCP OAuth**: evaluate built-in OAuth support as alternative to
  mcp-auth-proxy for public-facing deployments.

## Implementation Plan

### Phase 1: Core Library + API Validation

1. Create repo structure, packaging, CI/CD (adapted from ifcraftcorpus)
2. Copy + adapt `providers.py` and `embeddings.py` (rename to `vector_index.py`)
3. Implement `scanner.py` -- frontmatter parsing, heading-based chunking,
   `ChunkStrategy` protocol
4. Implement `fts_index.py` -- generic FTS5 with `document_tags`, RRF hybrid
5. Implement `tracker.py` -- hash-based change detection
6. Implement `collection.py` -- thin facade tying it all together
7. Tests for all modules (fixtures with sample .md files covering: no
   frontmatter, partial frontmatter, malformed YAML, deep headings, unicode)
8. **Validate API**: attempt to configure `Collection` with ifcraftcorpus
   settings (`required_frontmatter=["title", "cluster"]`,
   `indexed_frontmatter_fields=["cluster", "topics"]`). If the API doesn't
   accommodate the corpus use case cleanly, fix it now.

### Phase 2: MCP Server + CI/CD

9. Implement `mcp_server.py` with all tools, `ToolAnnotations`, lifespan hooks
10. Implement `cli.py` -- `serve`, `index`, `search`, `reindex` commands
11. Configuration loading (env vars)
12. Docker + GitHub Actions + PyPI (adapted from ifcraftcorpus)
13. Validate against Obsidian vault (`pvliesdonk/obsidian.md`) as read-only
    collection
14. MCP tool integration tests using FastMCP test client

### Phase 3: Deploy + Write Support

15. Deploy to homelab (Traefik + mcp-auth-proxy)
16. Write support: `write`, `edit`, `delete` tools
17. `on_write` callback with git strategy
18. `rename` tool
19. Test with Obsidian vault in read-write mode
20. Evaluate YAML config need

### Phase 4: Publish + ifcraftcorpus Refactor

21. Publish markdown-mcp 1.0 to PyPI
22. Refactor ifcraftcorpus to depend on markdown-mcp
23. ifcraftcorpus becomes thin wrapper + domain tools + subagent prompts

## Testing Strategy

- **Fixtures**: `tests/fixtures/` directory with sample vault notes in several
  shapes: no frontmatter, minimal frontmatter, full frontmatter, malformed
  YAML, deeply nested headings, unicode, empty files.
- **Unit tests**: scanner (frontmatter parsing, chunking, required_frontmatter
  filtering), FTS index (CRUD, search, tag filtering, RRF hybrid), change
  tracker (detect changes, update state), vector index (add, search,
  save/load, metadata consistency).
- **Integration tests**: Collection end-to-end (scan -> index -> search ->
  reindex), write + reindex roundtrip (write makes content searchable),
  MCP server tool invocations via FastMCP test client.
- **Regression tests**: hybrid score ordering (verify RRF produces sensible
  merged rankings), document identity (same filename in different folders),
  frontmatter-less files indexed correctly.
- **Coverage**: enforce with `coverage.py` `fail_under` (same pattern as
  ifcraftcorpus).

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| VRAM contention (Ollama on RTX 4060 8GB) | `cpu_only` mode, batch embeddings |
| Vault scale (numpy in-memory) | Fine for thousands of notes. If tens of thousands, evaluate Qdrant. |
| Concurrent writes (Obsidian + MCP) | Use git as sync layer. MCP server should not write directly to live Obsidian vault without git in between. |
| FastMCP breaking changes | Pin `>=2.5,<3`. Monitor for 3.0 migration. |
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
| 10 | FastMCP | Pin `>=2.5,<3`; lifespan hooks; follow conventions | Proper init/teardown; forward-compatible |
| 11 | Write support | Separate frontmatter param; generic `on_write` callback | Git strategy as built-in; extensible for future strategies |
| 12 | Docker/CI | Bring early (Phase 2); adapt from ifcraftcorpus | Proven infrastructure, minimal changes needed |
| 13a | Error handling | Library raises; MCP catches and returns structured | Clean separation of concerns |
| 13b | Logging | Follow FastMCP conventions; `logging.getLogger(__name__)` | Standardized, no `print()` |
| 13c | Concurrency | Library sync; `asyncio.to_thread()` in MCP layer | Appropriate for single-user; async provider as future work |
| 13d | FTS5 schema | `path`, `title`, `folder`, `heading`, `content` | Generic; domain filtering via `document_tags` |
| 13e | File extension | Include `.md` in document identifier | Unambiguous, matches filesystem |
| 14 | Python library use | Document as use case; `Collection` is primary API | MCP is one consumer; LangChain wrapper is downstream |
| 15 | Rename | Include in design, defer to Phase 2-3 | Touches every layer; not critical for initial release |
| 16 | Tool semantics | Mirror Claude Code Read/Write/Edit; MCP `ToolAnnotations` | Familiar to LLMs; `delete` marked destructive |
