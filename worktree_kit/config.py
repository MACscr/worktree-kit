"""TOML config loading and repository detection.

User config lives in ~/.worktree-kit.toml. A .worktree-kit.toml in the
worktree overrides it. Built-in defaults fill anything neither file sets.
"""

from __future__ import annotations

import copy
import os
import re
import subprocess
import tomllib
from pathlib import Path
from typing import Any


DEFAULTS: dict[str, Any] = {
    "general": {
        "caddy_sites_dir": "~/.worktree-kit/sites",
        "caddyfile": "~/.worktree-kit/Caddyfile",
        "php_fpm_socket": "/opt/homebrew/var/run/php-fpm.sock",
        "tld": "test",
    },
    "repos": {},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge *override* into *base*, returning a new dict."""
    merged = dict(base)
    for key, val in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(val, dict):
            merged[key] = _deep_merge(merged[key], val)
        else:
            merged[key] = val
    return merged


def load_config(paths: list[Path] | None = None) -> dict[str, Any]:
    """Load and merge config from defaults, then each existing file in *paths*.

    When *paths* is omitted, ~/.worktree-kit.toml is read first and a
    .worktree-kit.toml in the current directory overrides it. Pass an explicit
    list to read those files instead (tests do this so they never touch the
    real user config). WORKTREE_KIT_SITES_DIR and WORKTREE_KIT_TLD apply last.
    """
    config = copy.deepcopy(DEFAULTS)

    if paths is None:
        paths = [
            Path.home() / ".worktree-kit.toml",
            Path.cwd() / ".worktree-kit.toml",
        ]

    for path in paths:
        if path.is_file():
            with open(path, "rb") as f:
                config = _deep_merge(config, tomllib.load(f))

    if val := os.environ.get("WORKTREE_KIT_SITES_DIR"):
        config["general"]["caddy_sites_dir"] = val
    if val := os.environ.get("WORKTREE_KIT_TLD"):
        config["general"]["tld"] = val

    return config


def _git_remote_url() -> str:
    """Return the origin remote URL or empty string."""
    try:
        result = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return ""


def match_repo(
    config: dict[str, Any],
    *,
    remote_url: str,
    dir_name: str,
    full_path: str,
) -> str | None:
    """Return the repo key for this worktree, or None.

    path_match is checked against the full path for every repo first, because
    it is the most specific signal. Then each repo is tested in config order:
    remote_match against the origin URL, then dir_match against the directory
    name. A repo that sets neither pattern uses its key for both. Patterns are
    unanchored regular expressions.
    """
    for repo_name, repo_conf in config.get("repos", {}).items():
        if path_match := repo_conf.get("path_match"):
            if re.search(path_match, full_path):
                return repo_name

    for repo_name, repo_conf in config.get("repos", {}).items():
        if remote_url and re.search(repo_conf.get("remote_match", repo_name), remote_url):
            return repo_name
        if re.search(repo_conf.get("dir_match", repo_name), dir_name):
            return repo_name

    return None


def detect_repo(config: dict[str, Any]) -> str | None:
    """Auto-detect which configured repo the current directory belongs to."""
    return match_repo(
        config,
        remote_url=_git_remote_url(),
        dir_name=Path.cwd().name,
        full_path=str(Path.cwd()),
    )


def get_repo_config(config: dict[str, Any], repo_name: str) -> dict[str, Any]:
    """Return the [repos.<name>] section."""
    return config.get("repos", {}).get(repo_name, {})


def source_path(config: dict[str, Any], repo_name: str) -> Path | None:
    """Return the expanded source path for a repo (where to copy .env from)."""
    repo_conf = get_repo_config(config, repo_name)
    src = repo_conf.get("source")
    if not src:
        return None
    return Path(os.path.expanduser(src))
