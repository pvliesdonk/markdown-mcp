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
        groupmod -o -g "$TARGET_GID" appuser || echo "WARNING: groupmod failed, GID may conflict" >&2
    fi
    if [ "$cur_uid" != "$TARGET_UID" ]; then
        usermod -o -u "$TARGET_UID" -g "$TARGET_GID" appuser || echo "WARNING: usermod failed, UID may conflict" >&2
    fi

    # Fix ownership of data directories — volumes may arrive root-owned.
    chown -R appuser:appuser /data

    exec gosu appuser "$@"
fi

# Already running as non-root (e.g. user: directive in compose).
exec "$@"
