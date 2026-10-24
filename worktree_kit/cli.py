"""Command-line entry points for setup, archive, serve, and purge-herd."""

from __future__ import annotations

import argparse
import sys


def setup() -> None:
    """Entry point for worktree-setup."""
    parser = argparse.ArgumentParser(
        description="Set up a Laravel worktree: copy .env, install deps, create test DB, serve it",
    )
    parser.add_argument(
        "--fresh", action="store_true",
        help="Remove vendor/ and node_modules/ first so they are re-cloned "
             "(reclaims the disk an older worktree's private copies hold)",
    )
    args = parser.parse_args()

    from . import setup as _setup
    try:
        _setup.run(fresh=args.fresh)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def archive() -> None:
    """Entry point for worktree-archive."""
    parser = argparse.ArgumentParser(
        description="Clean up a worktree: drop test databases and Redis keys, drop its site",
    )
    parser.parse_args()

    from . import archive as _archive
    try:
        _archive.run()
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def purge_herd() -> None:
    """Entry point for worktree-purge-herd."""
    parser = argparse.ArgumentParser(
        description="Remove the per-site symlinks, nginx configs, and certs Herd generated",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="Actually delete (default is a dry run)",
    )
    parser.add_argument(
        "--dead-only", action="store_true",
        help="Only sites whose worktree directory no longer exists",
    )
    args = parser.parse_args()

    from . import purge_herd as _purge
    try:
        _purge.run(apply=args.apply, dead_only=args.dead_only)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


def serve() -> None:
    """Entry point for worktree-serve."""
    parser = argparse.ArgumentParser(
        description="Register this worktree's domain with Caddy",
    )
    parser.add_argument(
        "--no-open", action="store_true",
        help="Don't open the site in a browser",
    )
    args = parser.parse_args()

    from . import serve as _serve
    try:
        _serve.run(open_browser=not args.no_open)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
