# Git Integration

The `git` module provides auto-commit and push functionality for write operations. When configured, every write triggers a git commit and deferred push.

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
        - flush
        - close

::: markdown_vault_mcp.git.git_write_strategy
