""".env file manipulation."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any


def env_get(key: str, path: str | Path = ".env") -> str | None:
    """Read a value from a .env file. Returns None if key not found."""
    env_path = Path(path)
    if not env_path.is_file():
        return None
    for line in env_path.read_text().splitlines():
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1]
    return None


def env_set(key: str, value: str, path: str | Path = ".env") -> None:
    """Update or append a key=value pair in a .env file."""
    env_path = Path(path)
    pattern = re.compile(rf"^{re.escape(key)}=.*$", re.MULTILINE)

    if env_path.is_file():
        content = env_path.read_text()
        if pattern.search(content):
            content = pattern.sub(f"{key}={value}", content)
            env_path.write_text(content)
            return

    # Key not found (or file doesn't exist) — append
    with open(env_path, "a") as f:
        f.write(f"\n{key}={value}\n")


def env_unset(key: str, path: str | Path = ".env") -> bool:
    """Remove a key from a .env file. True if it was present."""
    env_path = Path(path)
    if not env_path.is_file():
        return False
    pattern = re.compile(rf"^{re.escape(key)}=.*\n?", re.MULTILINE)
    content = env_path.read_text()
    updated = pattern.sub("", content)
    if updated == content:
        return False
    env_path.write_text(updated)
    return True


def update_worktree_env(worktree_dir: str, repo_config: dict[str, Any], tld: str, env_path: str | Path = ".env") -> None:
    """Update all configured env_keys for a worktree domain.

    Default key mappings, where {domain} is the repo's worktree domain
    (`feat-auth.myapp.test`, or `feat-auth.test` when the repo sets no domain):

        SESSION_DOMAIN -> .{domain}   (leading dot for subdomains)
        APP_URL        -> https://{domain}
        APP_DOMAIN     -> {domain}
    """
    # Imported here rather than at module scope: caddy imports nothing from
    # this module, but keeping the dependency one-way avoids a future cycle.
    from .caddy import worktree_domain

    domain = worktree_domain(worktree_dir, repo_config, tld)
    value_map = {
        "SESSION_DOMAIN": f".{domain}",
        "APP_URL": f"https://{domain}",
        "APP_DOMAIN": domain,
    }

    for key in repo_config.get("env_keys", ["SESSION_DOMAIN", "APP_URL", "APP_DOMAIN"]):
        if key in value_map and env_get(key, env_path) is not None:
            env_set(key, value_map[key], env_path)
            print(f"  Updated {key} to: {value_map[key]}")

    # Worktree domains nest under the repo's own domain, so every worktree is a
    # subdomain of one parent. SESSION_COOKIE defaults to a slug of APP_NAME --
    # identical everywhere -- and a cookie set on the parent is sent to all of
    # them, so worktrees silently overwrite each other's sessions and a request
    # can be answered against another worktree's login. Naming the cookie after
    # the worktree keeps them separate.
    if env_get("SESSION_DOMAIN", env_path) is not None:
        cookie = f"{worktree_dir}-session"
        env_set("SESSION_COOKIE", cookie, env_path)
        print(f"  Updated SESSION_COOKIE to: {cookie}")

    # Imported here rather than at module scope: redis reads .env through this
    # module, so a top-level import would be circular.
    from . import redis

    # Shared cache stores (redis/memcached) collide across worktrees without a
    # unique prefix — cached content bakes in another worktree's APP_URL.
    if env_get("CACHE_STORE", env_path) in ("redis", "memcached"):
        cache_prefix = redis.worktree_cache_prefix(worktree_dir)
        env_set("CACHE_PREFIX", cache_prefix, env_path)
        print(f"  Updated CACHE_PREFIX to: {cache_prefix}")

    # Every worktree points at the same Redis server, and REDIS_PREFIX defaults
    # to a slug of APP_NAME — identical in each one. That shares the whole
    # keyspace: sessions overwrite each other, and workers pop jobs off a queue
    # any worktree can push to, running one branch's job against another's code
    # and database. Namespacing the connection isolates cache, sessions, queues,
    # and locks together.
    if redis.uses_redis(env_path):
        redis_prefix = redis.worktree_prefix(worktree_dir)
        env_set("REDIS_PREFIX", redis_prefix, env_path)
        print(f"  Updated REDIS_PREFIX to: {redis_prefix}")

        # Horizon swaps REDIS_PREFIX for its own prefix, which also defaults to
        # a slug of APP_NAME. Left shared, every worktree's Horizon registers in
        # one dashboard and failed-job list, and horizon:terminate stops the
        # other worktrees' workers on the same machine.
        if (Path(env_path).parent / "config" / "horizon.php").is_file():
            horizon_prefix = redis.worktree_horizon_prefix(worktree_dir)
            env_set("HORIZON_PREFIX", horizon_prefix, env_path)
            print(f"  Updated HORIZON_PREFIX to: {horizon_prefix}")
