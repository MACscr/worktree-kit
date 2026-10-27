"""Worktree directory names and domains. Does not write a Caddy site."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worktree_kit import caddy


class NameTests(unittest.TestCase):
    def test_regular_directory_name(self):
        self.assertEqual(
            caddy.detect_worktree_dir(Path("/code/myapp-feat-auth")),
            "myapp-feat-auth",
        )

    def test_nested_directory_uses_the_leaf_name(self):
        path = Path("/code/myapp/worktrees/feat-auth")
        self.assertEqual(caddy.detect_worktree_dir(path), "feat-auth")

    def test_domain_without_a_base(self):
        self.assertEqual(caddy.worktree_domain("feat-auth", {}, "test"), "feat-auth.test")

    def test_domain_nests_under_the_repo_domain(self):
        repo = {"domain": "myapp.test"}
        self.assertEqual(
            caddy.worktree_domain("feat-auth", repo, "test"),
            "feat-auth.myapp.test",
        )

    def test_source_checkout_keeps_the_base_domain(self):
        repo = {"domain": "myapp.test"}
        self.assertEqual(caddy.worktree_domain("myapp", repo, "test"), "myapp.test")


if __name__ == "__main__":
    unittest.main()
