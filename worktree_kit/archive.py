"""worktree-archive: worktree teardown orchestration."""

from __future__ import annotations

from pathlib import Path

from . import caddy, config, db, redis


def run() -> None:
    """Clean up a worktree: drop test databases and Redis keys, drop its site."""
    cfg = config.load_config()
    repo_name = config.detect_repo(cfg)
    repo_conf = config.get_repo_config(cfg, repo_name) if repo_name else {}
    worktree_dir = caddy.detect_worktree_dir()

    # 1. Drop this worktree's test database, and only that one.
    #
    # The name has to be the one setup derives for this directory. A value
    # copied from the source checkout, or one equal to DB_DATABASE, is how
    # archive would destroy a database it does not own.
    skip_test_db = repo_conf.get("skip_test_db", False)

    if not skip_test_db:
        if Path(".env").is_file():
            test_db_name = db.archive_test_db_name(worktree_dir, repo_conf)
            if test_db_name:
                print(f"Cleaning up test databases for: {test_db_name}")
                db.drop_test_dbs(test_db_name)
                print("  Test database cleanup complete")
            else:
                print(
                    "  Refusing to drop a test database: TEST_DB_DATABASE is not "
                    "the name derived for this worktree"
                )
        else:
            print("  No .env file, skipping DB cleanup")
    else:
        print("  Skipping test database cleanup (disabled for this repo)")

    # 2. Reclaim this worktree's Redis keyspace
    if Path(".env").is_file():
        prefix = redis.owned_prefix(worktree_dir)
        if prefix:
            print(f"Cleaning up Redis keys for: {worktree_dir}")
            redis.drop_keys(prefix)
        else:
            print("  No worktree-owned Redis prefix found, skipping Redis cleanup")

    # 3. Unregister this worktree's Caddy site
    tld = cfg.get("general", {}).get("tld", "test")
    domain = caddy.worktree_domain(worktree_dir, repo_conf, tld)
    print(f"Taking {domain} offline...")
    caddy.unserve(cfg, worktree_dir, domain)
