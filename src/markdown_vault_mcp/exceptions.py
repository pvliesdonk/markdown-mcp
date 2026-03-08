"""Exception types for markdown-vault-mcp."""


class MarkdownMCPError(Exception):
    """Base exception for markdown-vault-mcp."""


class DocumentNotFoundError(MarkdownMCPError):
    """Document path does not exist on disk."""


class ReadOnlyError(MarkdownMCPError):
    """Write operation attempted on read-only collection."""


class EditConflictError(MarkdownMCPError):
    """old_text not found or appears more than once."""


class DocumentExistsError(MarkdownMCPError):
    """Target path already exists (e.g., rename destination)."""
