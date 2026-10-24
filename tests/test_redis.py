"""Redis prefix derivation. Does not open a connection."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worktree_kit import redis


class PrefixTests(unittest.TestCase):
    def test_prefixes_nest_under_the_worktree_name(self):
        self.assertEqual(redis.worktree_prefix("feat-auth"), "feat-auth:")
        self.assertEqual(redis.worktree_cache_prefix("feat-auth"), "feat-auth:cache:")
        self.assertEqual(redis.worktree_horizon_prefix("feat-auth"), "feat-auth:horizon:")

    def test_owned_prefix_accepts_only_generated_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("REDIS_PREFIX=feat-auth:\n")
            self.assertEqual(redis.owned_prefix("feat-auth", path), "feat-auth:")
            path.write_text("REDIS_PREFIX=feat-auth-\n")
            self.assertIsNone(redis.owned_prefix("feat-auth", path))
            path.write_text("REDIS_PREFIX=shared\n")
            self.assertIsNone(redis.owned_prefix("feat-auth", path))
            path.write_text("REDIS_HOST=127.0.0.1\n")
            self.assertIsNone(redis.owned_prefix("feat-auth", path))

    def test_uses_redis_signals(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("CACHE_STORE=file\n")
            self.assertFalse(redis.uses_redis(path))
            path.write_text("QUEUE_CONNECTION=redis\n")
            self.assertTrue(redis.uses_redis(path))
            path.write_text("APP_NAME=myapp\n")
            self.assertFalse(redis.uses_redis(path))
            horizon = Path(tmp) / "config" / "horizon.php"
            horizon.parent.mkdir()
            horizon.write_text("<?php\n")
            self.assertTrue(redis.uses_redis(path))


if __name__ == "__main__":
    unittest.main()
