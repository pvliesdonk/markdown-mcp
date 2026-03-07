# markdown-mcp

Generic markdown collection MCP server with FTS5 + semantic search, frontmatter-aware indexing, and incremental reindexing.

## Design

The authoritative design specification lives at [`docs/design.md`](docs/design.md). All implementation must conform to this spec. When in doubt, the design doc wins.

## Project Structure

```
src/markdown_mcp/
  scanner.py        -- file discovery, frontmatter parsing, chunking
  fts_index.py      -- SQLite FTS5 schema, BM25 search
  vector_index.py   -- numpy embeddings, cosine similarity
  providers.py      -- embedding provider ABC + implementations
  tracker.py        -- hash-based change detection
  collection.py     -- thin facade: init, lazy loading, public API
  config.py         -- configuration loading
  mcp_server.py     -- generic FastMCP server with tool annotations
  cli.py            -- CLI entry point
```

## Conventions

- Python 3.10+
- `uv` for package management, `ruff` for linting/formatting (line length 88)
- `hatchling` build backend
- Conventional commits: `feat:`, `fix:`, `docs:`, `refactor:`, `test:`, `chore:`
- Google-style docstrings on all public functions
- `logging.getLogger(__name__)` throughout, no `print()`
- Type hints everywhere
- Tests: `pytest` with fixtures in `tests/fixtures/`

## Reference

This project is extracted from [`pvliesdonk/if-craft-corpus`](https://github.com/pvliesdonk/if-craft-corpus). See the design doc's Reference Code section for the mapping between source files.

## Key Design Decisions

- Document identity: relative path with `.md` extension
- Frontmatter: optional by default, `required_frontmatter` config to enforce
- Hybrid search: Reciprocal Rank Fusion (RRF)
- Tool semantics: mirror Claude Code Read/Write/Edit patterns
- Library is sync; MCP layer uses `asyncio.to_thread()`
- Full decision log in `docs/design.md` appendix
