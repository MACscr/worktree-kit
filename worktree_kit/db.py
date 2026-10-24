"""Test database naming, creation, and cleanup."""

from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path
from typing import Any

from . import env

# MySQL rejects any identifier over 64 characters (ERROR 1059).
MAX_IDENTIFIER_LEN = 64

# Laravel's parallel testing gives each worker its own "{name}_test_{n}"
# database, so the name itself has to leave room for that tail. Three digits
# covers far more workers than a development machine runs.
PARALLEL_SUFFIX_RESERVE = len("_test_999")

MAX_TEST_DB_NAME_LEN = MAX_IDENTIFIER_LEN - PARALLEL_SUFFIX_RESERVE

# Width of the collision-guard tail: "_" plus 6 hex chars.
_HASH_LEN = 6
_HASH_TAIL_LEN = _HASH_LEN + 1

# Letters, digits, and underscore only. That is what the derived names are,
# and it is the charset that can be placed in a backticked identifier and in
# a quoted LIKE pattern without breaking out of either.
_IDENTIFIER = re.compile(r"^[A-Za-z0-9_]+$")


def _sanitize_suffix(worktree_dir: str, repo_config: dict[str, Any]) -> str:
    """Strip repo name from worktree dir to get a unique suffix.

    Example: "myapp-feat-auth" -> "feat_auth" when dir_match is "myapp".
    """
    suffix = worktree_dir
    # Strip the dir_match pattern (repo name) from the worktree dir
    dir_match = repo_config.get("dir_match", "")
    if dir_match:
        suffix = re.sub(dir_match, "", suffix)
    # Clean leading/trailing separators, normalize
    suffix = re.sub(r"^[-_]+", "", suffix)
    suffix = suffix.replace("-", "_").lower()
    return suffix


def _fit_to_limit(name: str, suffix: str) -> str:
    """Shorten *name* so it and its parallel-worker copies fit MySQL's limit.

    Long branch names overflow the 64-character cap easily. Plain truncation
    is not safe: two branches sharing a long prefix would collapse onto one
    database and each one's test run would wipe the other's data. So the tail
    is replaced with a short hash of the full
    suffix, which keeps the readable head while making a collision require a
    hash collision rather than a shared prefix.

    The limit is MAX_TEST_DB_NAME_LEN, not 64: a name that only just fits
    still breaks `artisan test --parallel`, whose "_test_{n}" copies overflow.

    Names already within the limit are returned untouched, so existing
    databases keep their current names.
    """
    if len(name) <= MAX_TEST_DB_NAME_LEN:
        return name

    digest = hashlib.sha256(suffix.encode()).hexdigest()[:_HASH_LEN]
    head = name[: MAX_TEST_DB_NAME_LEN - _HASH_TAIL_LEN]
    # Avoid a doubled or trailing separator where the cut lands on one.
    head = head.rstrip("_")
    return f"{head}_{digest}"


def test_db_name(worktree_dir: str, repo_config: dict[str, Any], env_path: str | Path = ".env") -> str:
    """Generate the test database name for a worktree.

    Returns "{base}_pest" for master or "{base}_pest_{suffix}" for feature
    worktrees, shortened with a hash tail if it and its parallel-testing copies
    would exceed MySQL's 64-character identifier limit.
    """
    base_db = env.env_get("DB_DATABASE", env_path) or "laravel"
    suffix = _sanitize_suffix(worktree_dir, repo_config)
    if suffix:
        return _fit_to_limit(f"{base_db}_pest_{suffix}", suffix)
    return _fit_to_limit(f"{base_db}_pest", base_db)


def is_safe_identifier(name: str) -> bool:
    """True when *name* can be interpolated into SQL as an identifier."""
    return bool(name) and len(name) <= MAX_IDENTIFIER_LEN and _IDENTIFIER.fullmatch(name) is not None


def like_literal(name: str) -> str:
    """Escape LIKE wildcards so *name* matches literally.

    `_` is a single-character wildcard and `%` is the rest of the string.
    A name this tool generated always contains underscores, so an unescaped
    prefix would list other schemas before the exact-match check ran.
    """
    return name.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def may_drop_test_db(candidate: str, expected: str, base: str | None) -> bool:
    """True when archive may drop *candidate*.

    The only trusted name is the one setup derives for this worktree. Anything
    else may be the source checkout's test database, copied in with `.env`
    before setup finished. The name also has to differ from `DB_DATABASE`
    and be a plain identifier.
    """
    if not candidate or candidate != expected:
        return False
    if base and candidate == base:
        return False
    return is_safe_identifier(candidate)


def archive_test_db_name(
    worktree_dir: str,
    repo_config: dict[str, Any],
    env_path: str | Path = ".env",
) -> str | None:
    """Return the test database archive may drop, or None to refuse."""
    expected = test_db_name(worktree_dir, repo_config, env_path)
    recorded = env.env_get("TEST_DB_DATABASE", env_path)
    candidate = recorded if recorded else expected
    return candidate if may_drop_test_db(candidate, expected, env.env_get("DB_DATABASE", env_path)) else None


def schemas_like_sql(db_name: str) -> str | None:
    """SELECT that lists *db_name* and names starting with it, or None if unsafe."""
    if not is_safe_identifier(db_name):
        return None
    literal = like_literal(db_name)
    return (
        "SELECT schema_name FROM information_schema.schemata "
        f"WHERE schema_name LIKE '{literal}%';"
    )


def _mysql_cmd(sql: str, env_path: str | Path = ".env") -> subprocess.CompletedProcess:
    """Execute a MySQL command using credentials from .env."""
    host = env.env_get("DB_HOST", env_path) or "127.0.0.1"
    port = env.env_get("DB_PORT", env_path) or "3306"
    user = env.env_get("DB_USERNAME", env_path) or "root"
    password = env.env_get("DB_PASSWORD", env_path) or ""

    cmd = ["mysql", f"-h{host}", f"-P{port}", f"-u{user}", "-e", sql]
    environ = dict(__import__("os").environ)
    if password:
        environ["MYSQL_PWD"] = password

    return subprocess.run(cmd, capture_output=True, text=True, env=environ)


def create_test_db(db_name: str, env_path: str | Path = ".env") -> bool:
    """Create a test database if it doesn't exist. Returns True on success."""
    if not is_safe_identifier(db_name):
        print(f"  Warning: Refusing to create a database named {db_name!r}")
        return False
    sql = f"CREATE DATABASE IF NOT EXISTS `{db_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
    result = _mysql_cmd(sql, env_path)
    if result.returncode == 0:
        print(f"  Test database created/verified: {db_name}")
        return True
    print(f"  Warning: Could not create test database: {result.stderr.strip()}")
    return False


def drop_test_dbs(db_name: str, env_path: str | Path = ".env") -> None:
    """Drop a test database and its parallel-worker copies ({db_name}_test_{n}).

    Matching is exact on purpose. A bare prefix match also reaches every
    worktree whose name extends this one: archiving the database for "api"
    would drop "api_v3"'s databases, and archiving a "{base}_pest" master
    would drop every feature worktree's.
    """
    # LIKE only narrows the listing. "_" and "%" are wildcards there, so the
    # prefix is escaped; the regex is what decides ownership.
    sql = schemas_like_sql(db_name)
    if sql is None:
        print(f"  Warning: Refusing to drop a database named {db_name!r}")
        return
    result = _mysql_cmd(sql, env_path)
    if result.returncode != 0:
        print(f"  Warning: Could not list test databases: {result.stderr.strip()}")
        return

    owned = re.compile(rf"{re.escape(db_name)}(_test_\d+)?")
    for line in result.stdout.strip().splitlines():
        db = line.strip()
        # Also skips the column header mysql prints first.
        if not owned.fullmatch(db):
            continue
        drop_result = _mysql_cmd(f"DROP DATABASE IF EXISTS `{db}`;", env_path)
        if drop_result.returncode == 0:
            print(f"  Dropped database: {db}")
        else:
            print(f"  Warning: Could not drop {db}: {drop_result.stderr.strip()}")
