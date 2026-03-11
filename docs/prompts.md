# MCP Prompts

Prompt templates guide the LLM through multi-step workflows using the vault tools. Write prompts (`research`, `discuss`) are only available when `MARKDOWN_VAULT_MCP_READ_ONLY=false`.

## Quick Reference

| Prompt | Parameters | Category | Description |
|--------|------------|----------|-------------|
| [`summarize`](#summarize) | `path` | Read | Structured summary of a document |
| [`research`](#research) | `topic` | Write | Search, synthesize, and create a research note |
| [`discuss`](#discuss) | `path` | Write | Analyze and suggest improvements using `edit` |
| [`related`](#related) | `path` | Read | Find related notes and suggest cross-references |
| [`compare`](#compare) | `path1`, `path2` | Read | Side-by-side comparison of two documents |

---

## `summarize`

Read a document and produce a structured summary with key themes and takeaways.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | string | Relative path to the document to summarize |

**Workflow:** Calls `read` on the given path, then produces a concise summary covering the document's main topics and key points.

## `research`

Search for a topic, synthesize findings across multiple documents, and create a new research note.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `topic` | string | The topic to research |

**Workflow:**

1. Calls `search` with the topic (uses hybrid mode if available)
2. Reads the top 3-5 results
3. Writes a structured summary with source links to `Research/{topic-slug}.md`

!!! note "Write prompt"
    This prompt creates a new document and is only available when `READ_ONLY=false`.

## `discuss`

Analyze a document and suggest improvements, applying changes via `edit` (not `write`).

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | string | Relative path to the document to discuss |

**Workflow:**

1. Calls `read` to review the document
2. Identifies specific improvements (factual corrections, clarity, structure, completeness)
3. Presents proposed changes to the user
4. Applies approved changes using `edit` calls

!!! note "Write prompt"
    This prompt modifies existing documents and is only available when `READ_ONLY=false`.

## `related`

Find related notes via search and suggest cross-references as markdown links.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path` | string | Relative path to the document to find related notes for |

**Workflow:**

1. Calls `read` to extract main topics and key terms
2. Calls `search` using those terms (prefers semantic mode)
3. Presents a list of related documents with connection explanations

This is a read-only prompt — it does not modify any documents.

## `compare`

Read two documents and produce a side-by-side comparison.

**Parameters:**

| Parameter | Type | Description |
|-----------|------|-------------|
| `path1` | string | Relative path to the first document |
| `path2` | string | Relative path to the second document |

**Workflow:** Reads both documents and presents a comparison covering:

- What both documents agree on
- Where they differ or contradict
- Information present in one but absent from the other
