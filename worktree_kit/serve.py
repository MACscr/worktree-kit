"""worktree-serve: register the current worktree with Caddy."""

from __future__ import annotations

import subprocess

from . import caddy, config, env


def run(*, open_browser: bool = True) -> None:
    """Register this worktree's domain with Caddy, served by the shared PHP-FPM pool."""
    cfg = config.load_config()
    tld = cfg.get("general", {}).get("tld", "test")
    repo_name = config.detect_repo(cfg)
    repo_conf = config.get_repo_config(cfg, repo_name) if repo_name else {}
    worktree_dir = caddy.detect_worktree_dir()
    domain = caddy.worktree_domain(worktree_dir, repo_conf, tld)

    reloaded = caddy.serve(cfg, worktree_dir, domain, repo_conf=repo_conf)

    # Left by the pre-FPM setup, which gave each worktree its own port.
    env.env_unset("WORKTREE_PORT")

    if open_browser and reloaded:
        subprocess.run(["open", f"https://{domain}"], check=False)
