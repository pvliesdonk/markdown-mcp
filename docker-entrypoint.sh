#!/bin/sh
set -e

# If running as root, fix data directory ownership and drop privileges.
if [ "$(id -u)" = '0' ]; then
    TARGET_UID="${PUID:-1000}"
    TARGET_GID="${PGID:-1000}"

    # Update appuser UID/GID if they differ from build-time defaults.
    cur_uid="$(id -u appuser)"
    cur_gid="$(id -g appuser)"
    if [ "$cur_gid" != "$TARGET_GID" ]; then
        groupmod -g "$TARGET_GID" appuser 2>/dev/null || true
    fi
    if [ "$cur_uid" != "$TARGET_UID" ]; then
        usermod -u "$TARGET_UID" -g "$TARGET_GID" appuser 2>/dev/null || true
    fi

    # Fix ownership of data directories — volumes may arrive root-owned.
    chown -R appuser:appuser /data

    exec gosu appuser "$@"
fi

# Already running as non-root (e.g. user: directive in compose).
exec "$@"
