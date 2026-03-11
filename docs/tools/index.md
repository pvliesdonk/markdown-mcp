# MCP Tools

markdown-vault-mcp exposes 13 MCP tools. Write tools (`write`, `edit`, `delete`, `rename`) are only available when `MARKDOWN_VAULT_MCP_READ_ONLY=false`.

## Quick Reference

| Tool | Category | Description |
|------|----------|-------------|
| [`search`](#search) | Read | Hybrid full-text + semantic search with optional frontmatter filters |
| [`read`](#read) | Read | Read a document or attachment by relative path |
| [`list_documents`](#list_documents) | Read | List indexed documents and optionally attachments |
| [`list_folders`](#list_folders) | Read | List all folder paths in the vault |
| [`list_tags`](#list_tags) | Read | List all unique frontmatter tag values |
| [`stats`](#stats) | Read | Get collection statistics and capabilities |
| [`embeddings_status`](#embeddings_status) | Read | Check embedding provider and vector index status |
| [`reindex`](#reindex) | Admin | Force a full reindex of the vault |
| [`build_embeddings`](#build_embeddings) | Admin | Build or rebuild vector embeddings |
| [`write`](#write) | Write | Create or overwrite a document or attachment |
| [`edit`](#edit) | Write | Replace a unique text span in a document |
| [`delete`](#delete) | Write | Delete a document or attachment |
| [`rename`](#rename) | Write | Rename/move a document or attachment |

---

## Search & Discovery

### `search`

Find documents matching a query using full-text or semantic search.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `query` | string | required | Natural language or keyword query string |
| `limit` | int | `10` | Maximum results to return |
| `mode` | string | `"keyword"` | `"keyword"` (FTS5/BM25), `"semantic"` (vector similarity), or `"hybrid"` (reciprocal rank fusion) |
| `folder` | string | `null` | Restrict to documents under this folder path |
| `filters` | object | `null` | Filter by indexed frontmatter field values (e.g. `{"tags": "pacing"}`) |

**Returns:** List of result dicts ranked by relevance. Each contains: `path`, `title`, `folder`, `content` (matched chunk), `score`, `frontmatter`.

!!! tip "Choosing a search mode"
    - Use `mode="hybrid"` when semantic search is available — it combines keyword precision with semantic understanding
    - Use `mode="keyword"` for exact term matches
    - Use `mode="semantic"` for meaning-based similarity
    - Check `stats` to see if `semantic_search_available` is true

**Example usage:**

```json
{
  "query": "character development techniques",
  "mode": "hybrid",
  "limit": 5,
  "filters": {"tags": "craft"}
}
```

### `read`

Read the full content of a document or attachment by path.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | string | Relative path to the document or attachment (e.g. `"Journal/note.md"` or `"assets/diagram.pdf"`) |

**Returns:**

=== "Markdown document"

    ```json
    {
      "path": "Journal/note.md",
      "title": "My Note",
      "folder": "Journal",
      "content": "The markdown body...",
      "frontmatter": {"title": "My Note", "tags": ["journal"]},
      "modified_at": 1741564800.0
    }
    ```

=== "Attachment"

    ```json
    {
      "path": "assets/diagram.pdf",
      "mime_type": "application/pdf",
      "size_bytes": 12345,
      "content_base64": "<base64 string>",
      "modified_at": 1741564800.0
    }
    ```

### `list_documents`

List documents (and optionally attachments) in the collection.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `folder` | string | `null` | Return only documents in this folder |
| `pattern` | string | `null` | Unix glob matched against relative paths (e.g. `"Journal/*.md"`) |
| `include_attachments` | bool | `false` | When true, also returns non-`.md` files that match the configured allowlist |

**Returns:** List of info dicts. Every entry has a `kind` field (`"note"` or `"attachment"`). Body content is not included — call `read` for full text.

### `list_folders`

List all folder paths that contain documents. Use this to discover valid folder names for filtering `search` or `list_documents`. The root folder (top-level documents) is represented as an empty string `""`.

**Returns:** Sorted list of folder paths, e.g. `["", "Journal", "Projects"]`.

### `list_tags`

List all distinct values for a frontmatter field across the collection.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `field` | string | `"tags"` | Frontmatter field name to enumerate. Must match a field in `indexed_frontmatter_fields` (check `stats`) |

**Returns:** Sorted list of distinct string values, e.g. `["craft", "pacing", "worldbuilding"]`.

### `stats`

Get an overview of the collection's size, capabilities, and configuration. Call this at the start of a session to understand what the collection contains and what search modes are available.

**Returns:**

```json
{
  "document_count": 42,
  "chunk_count": 156,
  "folder_count": 5,
  "semantic_search_available": true,
  "indexed_frontmatter_fields": ["tags", "cluster"],
  "attachment_extensions": ["pdf", "png", "jpg"]
}
```

### `embeddings_status`

Check the embedding provider configuration and vector index status. Use this to diagnose why semantic search is unavailable.

**Returns:**

```json
{
  "available": true,
  "provider": "OllamaProvider",
  "chunk_count": 156,
  "path": "/data/embeddings/embeddings"
}
```

---

## Index Management

### `reindex`

Incrementally update the full-text search index to reflect file changes made outside this server. Only processes changed files — unchanged documents are skipped.

If semantic search is already active (vector index loaded), this also re-embeds changed documents automatically.

**Returns:** `{"added": 3, "modified": 1, "deleted": 0, "unchanged": 38}`

### `build_embeddings`

Build vector embeddings to enable semantic and hybrid search. This can be slow for large collections.

**Parameters:**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `force` | bool | `false` | When true, discards existing embeddings and rebuilds from scratch. Use only if the embedding model has changed |

**Returns:** `{"chunks_embedded": 156}`

!!! note "When to use"
    Call `build_embeddings` once to enable semantic search for the first time. After that, `reindex` handles incremental re-embedding automatically.

---

## Write Operations

!!! warning "Write tools require `MARKDOWN_VAULT_MCP_READ_ONLY=false`"
    These tools are hidden when the server is in read-only mode (the default).

### `write`

Create or overwrite a document or attachment.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | string | Relative path. Extension determines handling (`.md` = note, else attachment) |
| `content` | string | Full markdown body for `.md` files (excluding frontmatter). Ignored for attachments |
| `frontmatter` | object | Optional YAML frontmatter dict for `.md` files. Ignored for attachments |
| `content_base64` | string | Base64-encoded binary content for attachment files. Required when path is not `.md` |

**Returns:** `{"path": "Journal/note.md", "created": true}`

!!! warning
    `write` replaces the entire file — use `edit` for targeted changes to existing documents.

### `edit`

Make a targeted text replacement in an existing document.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | string | Relative path to the document |
| `old_text` | string | Exact text to replace. Must appear exactly once in the document |
| `new_text` | string | Replacement text |

**Returns:** `{"path": "Journal/note.md", "replacements": 1}`

!!! tip "Usage pattern"
    Always call `read` first to get the exact current text, then pass a portion of it as `old_text`. The match is exact and must appear only once. Frontmatter can be edited — `old_text` may span the YAML block.

### `delete`

Permanently delete a document or attachment. For `.md` documents, also removes from all search indices.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | string | Relative path to the document or attachment to delete |

**Returns:** `{"path": "Journal/old-note.md"}`

!!! danger
    This is irreversible unless git history exists. Confirm the path with the user before calling.

### `rename`

Rename a document or attachment, or move it to a different folder. Parent directories for the new path are created automatically.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `old_path` | string | Current relative path |
| `new_path` | string | Target relative path. Fails if `new_path` already exists |

**Returns:** `{"old_path": "drafts/idea.md", "new_path": "projects/idea.md"}`
