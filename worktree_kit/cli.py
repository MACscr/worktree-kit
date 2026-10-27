"""Command-line entry points for setup and archive."""

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
