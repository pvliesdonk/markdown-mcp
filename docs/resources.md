# MCP Resources

MCP resources expose vault metadata as structured JSON that clients can read directly without invoking tools. All resources return `application/json`.

## Quick Reference

| URI | Description |
|-----|-------------|
| [`config://vault`](#configvault) | Current collection configuration |
| [`stats://vault`](#statsvault) | Collection statistics |
| [`tags://vault`](#tagsvault) | All tags grouped by indexed field |
| [`tags://vault/{field}`](#tagsvaultfield) | Tags for a specific field |
| [`folders://vault`](#foldersvault) | All folder paths |
| [`toc://vault/{path}`](#tocvaultpath) | Table of contents for a document |

---

## `config://vault`

Current collection configuration and runtime state.

**Response:**

```json
{
  "source_dir": "/data/vault",
  "read_only": true,
  "indexed_fields": ["tags", "cluster"],
  "required_fields": [],
  "exclude_patterns": [".obsidian/**", ".trash/**"],
  "semantic_search_available": true,
  "attachment_extensions": ["pdf", "png", "jpg"]
}
```

## `stats://vault`

Collection statistics — document count, chunk count, and capabilities.

**Response:**

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

## `tags://vault`

All frontmatter tag values grouped by indexed field.

**Response:**

```json
{
  "tags": ["craft", "pacing", "worldbuilding"],
  "cluster": ["fiction", "non-fiction"]
}
```

## `tags://vault/{field}`

Tag values for a specific indexed frontmatter field. This is a URI template — replace `{field}` with the field name.

**Example:** `tags://vault/tags`

**Response:**

```json
["craft", "pacing", "worldbuilding"]
```

## `folders://vault`

All folder paths in the vault.

**Response:**

```json
["", "Journal", "Projects", "Research"]
```

The empty string `""` represents the root folder (top-level documents).

## `toc://vault/{path}`

Table of contents (heading outline) for a specific document. This is a URI template — replace `{path}` with the document's relative path.

**Example:** `toc://vault/Journal/note.md`

**Response:**

```json
[
  {"level": 1, "title": "My Note"},
  {"level": 2, "title": "Introduction"},
  {"level": 2, "title": "Main Points"},
  {"level": 3, "title": "First Point"},
  {"level": 2, "title": "Conclusion"}
]
```

The TOC prepends a synthetic H1 from the document title and deduplicates if the first real heading matches the title.
