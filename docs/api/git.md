# Git Integration

The `git` module provides:

- Auto-commit + deferred push for write operations (via `on_write`)
- Periodic pull (ff-only) primitives used by the server to keep the working tree up to date

## Quick Start

```python
from pathlib import Path
from markdown_vault_mcp import Collection, GitWriteStrategy

strategy = GitWriteStrategy(
    token="ghp_your_token",
    push_delay_s=30,
)

collection = Collection(
    source_dir=Path("/path/to/vault"),
    read_only=False,
    on_write=strategy,
)

# Writes are now auto-committed and pushed
collection.write("notes/new.md", "Hello world")

# Clean up on shutdown
collection.close()
```

## API Reference

::: markdown_vault_mcp.git.GitWriteStrategy
    options:
      members:
        - __init__
        - __call__
        - sync_once
        - start
        - stop
        - flush
        - close

::: markdown_vault_mcp.git.git_write_strategy
