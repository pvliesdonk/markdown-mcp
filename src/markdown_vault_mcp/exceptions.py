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


class ConcurrentModificationError(MarkdownMCPError):
    """Raised when if_match etag does not match the current file state."""

    def __init__(self, path: str, expected: str, actual: str) -> None:
        self.path = path
        self.expected = expected
        self.actual = actual
        super().__init__(
            f"Concurrent modification on {path}: "
            f"expected etag {expected!r}, actual {actual!r}"
        )


class ConfigurationError(MarkdownMCPError):
    """Invalid or unsupported configuration at startup."""
