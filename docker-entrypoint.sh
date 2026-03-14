#!/bin/sh
set -e

# If running as root, fix data directory ownership and drop privileges.
if [ "$(id -u)" = '0' ]; then
    TARGET_UID="${PUID:-1000}"
    TARGET_GID="${PGID:-1000}"

    # Reject UID/GID 0 — matches the build-time APP_UID/APP_GID guard.
    if [ "$TARGET_UID" -eq 0 ] || [ "$TARGET_GID" -eq 0 ]; then
        echo "ERROR: PUID and PGID must be non-zero" >&2
        exit 1
    fi

    # Update appuser UID/GID if they differ from build-time defaults.
    cur_uid="$(id -u appuser)"
    cur_gid="$(id -g appuser)"
    if [ "$cur_gid" != "$TARGET_GID" ]; then
        groupmod -o -g "$TARGET_GID" appuser
    fi
    if [ "$cur_uid" != "$TARGET_UID" ]; then
        usermod -o -u "$TARGET_UID" -g "$TARGET_GID" appuser
    fi

    # Ensure state subdirectories exist inside the volume.
    # Dockerfile seeds these on first use, but externally-created or
    # pre-existing volumes may be empty.
    mkdir -p /data/state/embeddings /data/state/fastembed /data/state/fastmcp
    # Always fix ownership of state subdirs — mkdir creates as root,
    # but the conditional chown loop below may skip /data/state if it
    # was already owned by appuser from a previous run.
    chown appuser:appuser /data/state/*

    # Fix ownership — named volumes may arrive root-owned.
    # Only recurse into directories still owned by root, to avoid
    # touching bind-mounted vault files on every restart.
    chown appuser:appuser /data
    for _dir in /data/*; do
        if [ -d "$_dir" ] && [ "$(stat -c '%u' "$_dir")" = '0' ]; then
            chown -R appuser:appuser "$_dir"
        fi
    done

    exec gosu appuser "$@"
fi

# Already running as non-root (e.g. user: directive in compose).
exec "$@"
