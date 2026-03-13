# Guides

Step-by-step walkthroughs for common deployment scenarios. Each guide takes you from zero to a working configuration with a verification step at the end.

## Which guide do I need?

| I want to... | Guide |
|---|---|
| Understand git modes (managed, commit-only, no-git) | [Git Integration](git-integration.md) |
| Connect my Obsidian vault to Claude Desktop | [Claude Desktop](claude-desktop.md) |
| Enable write/edit operations with git auto-commit | [Claude Desktop](claude-desktop.md#step-2-enable-git-write-support) |
| Add semantic search to my vault | [Claude Desktop](claude-desktop.md#step-3-add-semantic-search) |
| Run the server in a Docker container | [Docker](docker.md) |
| Add git write support to a container | [Docker](docker.md#step-2-add-git-write-support) |
| Protect my server with a bearer token | [Authentication](authentication.md#bearer-token) |
| Protect my server with OIDC authentication | [Authentication](authentication.md#oidc) |
| Access my vault from desktop, mobile, AND Claude | [Obsidian Everywhere](obsidian-everywhere.md) |
| Use FastEmbed for local embeddings | [Embeddings](embeddings.md#fastembed) |
| Use Ollama for embeddings (CPU-only) | [Embeddings](embeddings.md#ollama) |
| Use OpenAI for embeddings | [Embeddings](embeddings.md#openai) |
| Set up OIDC with Authelia | [OIDC Providers](oidc-providers.md#authelia) |
| Set up OIDC with Keycloak | [OIDC Providers](oidc-providers.md#keycloak) |
| Set up OIDC with Google | [OIDC Providers](oidc-providers.md#google) |
| Set up OIDC with GitHub (via Keycloak) | [OIDC Providers](oidc-providers.md#github) |

## Prerequisites

All guides assume you have:

- A directory of markdown files (e.g., an Obsidian vault)
- Python 3.10+ installed (for local installs) or Docker (for container deployments)

For installation instructions, see [Installation](../installation.md). For the full environment variable reference, see [Configuration](../configuration.md).
