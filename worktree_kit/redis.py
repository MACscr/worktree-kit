"""Redis keyspace cleanup for worktrees."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path

from .env import env_get

# Serving goes through Caddy. Redis may still be the copy Herd installed.
# That redis-cli lives in Herd's bin directory, which is on PATH only in
# interactive shells, so a non-interactive archive would otherwise skip cleanup.
HERD_BIN_DIR = "~/Library/Application Support/Herd/bin"

# UNLINK takes the keys as arguments, so a worktree with a large cache would
# otherwise overflow the command line.
UNLINK_BATCH_SIZE = 500


def worktree_prefix(worktree_dir: str) -> str:
    """Return the Redis key prefix a worktree owns.

    The trailing colon is load-bearing. Separated with a hyphen, worktree
    "api" would own "api-", which also matches every key belonging to
    "api-v2" — archiving the first would wipe the second's cache, sessions,
    and queued jobs. A worktree directory name cannot contain a colon (it has
    to be a valid hostname), so ending the prefix with one makes it impossible
    for two worktrees' prefixes to nest.
    """
    return f"{worktree_dir}:"


def worktree_cache_prefix(worktree_dir: str) -> str:
    """Return the cache-store prefix a worktree owns."""
    return f"{worktree_dir}:cache:"


def worktree_horizon_prefix(worktree_dir: str) -> str:
    """Return the Horizon prefix a worktree owns.

    Nested under worktree_prefix so archive's sweep of REDIS_PREFIX reclaims
    Horizon's keys too.
    """
    return f"{worktree_dir}:horizon:"


def uses_redis(env_path: str | Path = ".env") -> bool:
    """Return whether the app talks to Redis at all.

    REDIS_HOST alone isn't a reliable signal: Laravel defaults it to 127.0.0.1,
    so an app can queue, cache, or keep sessions in Redis without setting it.
    """
    if env_get("REDIS_HOST", env_path) is not None:
        return True
    if (Path(env_path).parent / "config" / "horizon.php").is_file():
        return True
    return any(
        env_get(key, env_path) == "redis"
        for key in ("QUEUE_CONNECTION", "SESSION_DRIVER", "CACHE_STORE", "CACHE_DRIVER")
    )


def owned_prefix(worktree_dir: str, env_path: str | Path = ".env") -> str | None:
    """Return REDIS_PREFIX, but only when it is the colon prefix for this worktree.

    Any other value — hand-edited, or inherited from the source checkout —
    may be shared with another worktree, and sweeping a shared prefix would
    take their keys with it. The prefix ends in a colon so it cannot be a
    prefix of another worktree's name.
    """
    value = env_get("REDIS_PREFIX", env_path)
    if value == worktree_prefix(worktree_dir):
        return value
    return None


def _redis_cli() -> str | None:
    """Locate redis-cli, falling back to the copy Herd bundles.

    Herd's bin directory is only on PATH in interactive shells, and on a
    machine whose Redis still comes from Herd it is the only redis-cli.
    """
    if found := shutil.which("redis-cli"):
        return found

    bundled = Path(os.path.expanduser(HERD_BIN_DIR)) / "redis-cli"
    return str(bundled) if os.access(bundled, os.X_OK) else None


def _redis_cli_args(env_path: str | Path = ".env") -> list[str] | None:
    """Build the redis-cli invocation for the worktree's configured server.

    Returns None when redis-cli is unavailable or the worktree isn't using Redis.
    """
    if not uses_redis(env_path):
        return None

    cli = _redis_cli()
    if not cli:
        return None

    args = [cli, "-h", env_get("REDIS_HOST", env_path) or "127.0.0.1"]

    port = env_get("REDIS_PORT", env_path)
    if port:
        args += ["-p", port]

    password = env_get("REDIS_PASSWORD", env_path)
    if password and password not in ("null", "false", ""):
        args += ["-a", password, "--no-auth-warning"]

    return args


def _populated_databases(args: list[str]) -> list[str] | None:
    """Return the index of every non-empty database on the server, or None on error."""
    info = subprocess.run(
        args + ["INFO", "keyspace"],
        capture_output=True,
        text=True,
    )
    if info.returncode != 0:
        print(f"  Warning: Could not read Redis keyspace: {info.stderr.strip()}")
        return None

    return re.findall(r"^db(\d+):", info.stdout, re.MULTILINE)


def drop_keys(prefix: str, env_path: str | Path = ".env") -> None:
    """Delete every Redis key under a worktree's prefix, across all databases.

    Worktrees share one Redis server, so an archived worktree leaves its cache,
    session, queue, and lock keys behind forever. Nothing else reclaims them:
    sessions and cache entries expire, but queue lists and locks do not.

    Every database is swept because Laravel spreads one app across several —
    separate connections for the default, cache, and session stores, on
    databases 0, 1, and 2 by default. Cleaning only REDIS_DB would leave the
    cached HTML this is chiefly meant to reclaim sitting in database 1.
    """
    if not prefix:
        return

    args = _redis_cli_args(env_path)
    if args is None:
        print("  Skipping Redis cleanup (not configured or redis-cli unavailable)")
        return

    databases = _populated_databases(args)
    if databases is None:
        return

    deleted = 0
    for database in databases:
        db_args = args + ["-n", database]

        # SCAN rather than KEYS: it doesn't block the server on a large keyspace.
        scan = subprocess.run(
            db_args + ["--scan", "--pattern", f"{prefix}*"],
            capture_output=True,
            text=True,
        )
        if scan.returncode != 0:
            print(f"  Warning: Could not scan Redis db {database}: {scan.stderr.strip()}")
            continue

        # --scan matches with a glob, so re-check the prefix literally: a
        # worktree name containing * or ? would otherwise widen the pattern.
        keys = [k for k in scan.stdout.splitlines() if k.startswith(prefix)]

        for start in range(0, len(keys), UNLINK_BATCH_SIZE):
            batch = keys[start:start + UNLINK_BATCH_SIZE]
            # UNLINK reclaims memory on a background thread; DEL would block.
            delete = subprocess.run(
                db_args + ["UNLINK"] + batch,
                capture_output=True,
                text=True,
            )
            if delete.returncode == 0:
                deleted += len(batch)
            else:
                print(
                    f"  Warning: Could not delete Redis keys in db {database}: "
                    f"{delete.stderr.strip()}"
                )

    if deleted:
        print(f"  Deleted {deleted} Redis key(s) for prefix: {prefix}")
    else:
        print(f"  No Redis keys found for prefix: {prefix}")
