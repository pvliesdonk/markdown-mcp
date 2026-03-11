"""Shared hashing utilities for markdown-vault-mcp."""

from __future__ import annotations

import hashlib
from pathlib import Path  # noqa: TC003


def compute_file_hash(path: Path) -> str:
    """Compute the SHA256 hex digest of a file using chunked reads.

    Reads the file in 8 KiB chunks so that large files do not require
    loading the entire content into memory at once.

    Args:
        path: Absolute path to the file to hash.

    Returns:
        Lowercase hex-encoded SHA256 digest.

    Raises:
        OSError: If the file cannot be opened or read.
    """
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()
