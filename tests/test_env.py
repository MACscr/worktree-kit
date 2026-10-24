""".env reads and the worktree key rewrite. Uses a temp file only."""

from __future__ import annotations

import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worktree_kit import env


class EnvFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def write(self, text: str) -> Path:
        path = self.dir / ".env"
        path.write_text(text)
        return path

    def update(self, *args) -> None:
        with redirect_stdout(io.StringIO()):
            env.update_worktree_env(*args)

    def test_get_set_and_unset(self):
        path = self.write("APP_URL=https://myapp.test\nDB_DATABASE=appdb\n")
        self.assertEqual(env.env_get("APP_URL", path), "https://myapp.test")
        self.assertIsNone(env.env_get("MISSING", path))
        self.assertIsNone(env.env_get("APP_URL", self.dir / "nope"))

        env.env_set("APP_URL", "https://feat.test", path)
        env.env_set("APP_DOMAIN", "feat.test", path)
        self.assertEqual(env.env_get("APP_URL", path), "https://feat.test")
        self.assertEqual(env.env_get("APP_DOMAIN", path), "feat.test")
        self.assertEqual(env.env_get("DB_DATABASE", path), "appdb")

        self.assertTrue(env.env_unset("APP_DOMAIN", path))
        self.assertIsNone(env.env_get("APP_DOMAIN", path))
        self.assertFalse(env.env_unset("APP_DOMAIN", path))
        self.assertFalse(env.env_unset("APP_URL", self.dir / "nope"))

    def test_domain_keys_and_session_cookie(self):
        path = self.write(
            "SESSION_DOMAIN=myapp.test\n"
            "APP_URL=https://myapp.test\n"
            "APP_DOMAIN=myapp.test\n"
        )
        self.update(
            "feat-auth",
            {"domain": "myapp.test", "env_keys": ["SESSION_DOMAIN", "APP_URL", "APP_DOMAIN"]},
            "test",
            path,
        )
        self.assertEqual(env.env_get("APP_URL", path), "https://feat-auth.myapp.test")
        self.assertEqual(env.env_get("SESSION_DOMAIN", path), ".feat-auth.myapp.test")
        self.assertEqual(env.env_get("APP_DOMAIN", path), "feat-auth.myapp.test")
        self.assertEqual(env.env_get("SESSION_COOKIE", path), "feat-auth-session")
        self.assertIsNone(env.env_get("REDIS_PREFIX", path))
        self.assertIsNone(env.env_get("CACHE_PREFIX", path))

    def test_absent_mapped_keys_are_not_created(self):
        path = self.write("SESSION_DOMAIN=old\nAPP_URL=https://old\n")
        self.update("feat", {}, "test", path)
        self.assertEqual(env.env_get("APP_URL", path), "https://feat.test")
        self.assertIsNone(env.env_get("APP_DOMAIN", path))
        self.assertEqual(env.env_get("SESSION_COOKIE", path), "feat-session")

    def test_redis_cache_and_horizon_prefixes(self):
        path = self.write(
            "SESSION_DOMAIN=old\n"
            "CACHE_STORE=redis\n"
            "REDIS_HOST=127.0.0.1\n"
        )
        (self.dir / "config").mkdir()
        (self.dir / "config" / "horizon.php").write_text("<?php\n")
        self.update("feat-auth", {}, "test", path)
        self.assertEqual(env.env_get("REDIS_PREFIX", path), "feat-auth:")
        self.assertEqual(env.env_get("CACHE_PREFIX", path), "feat-auth:cache:")
        self.assertEqual(env.env_get("HORIZON_PREFIX", path), "feat-auth:horizon:")


if __name__ == "__main__":
    unittest.main()
