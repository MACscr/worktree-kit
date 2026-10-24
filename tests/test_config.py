"""Config merge and repo matching. Never reads the real user config."""

from __future__ import annotations

import os
import sys
import tempfile
import tomllib
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worktree_kit import config


@contextmanager
def isolated_env(**overrides: str):
    """Hide the developer machine's WORKTREE_KIT_* variables."""
    with patch.dict(os.environ, overrides):
        for key in ("WORKTREE_KIT_SITES_DIR", "WORKTREE_KIT_TLD"):
            if key not in overrides:
                os.environ.pop(key, None)
        yield


class DefaultsTests(unittest.TestCase):
    def test_returned_config_is_independent_of_the_defaults(self):
        with isolated_env():
            cfg = config.load_config(paths=[])
            cfg["general"]["tld"] = "changed"
            cfg["repos"]["myapp"] = {"source": "/code/myapp"}
        self.assertEqual(config.DEFAULTS["general"]["tld"], "test")
        self.assertEqual(config.DEFAULTS["repos"], {})

    def test_env_override_does_not_mutate_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "repos-only.toml"
            path.write_text('[repos.myapp]\nsource = "/code/myapp"\n')
            with isolated_env(WORKTREE_KIT_TLD="localhost"):
                cfg = config.load_config(paths=[path])
            self.assertEqual(cfg["general"]["tld"], "localhost")
            self.assertEqual(cfg["repos"]["myapp"]["source"], "/code/myapp")
        self.assertEqual(config.DEFAULTS["general"]["tld"], "test")


class LoadConfigTests(unittest.TestCase):
    def test_missing_files_leave_the_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            with isolated_env():
                cfg = config.load_config(paths=[Path(tmp) / "missing.toml"])
        self.assertEqual(cfg["general"], config.DEFAULTS["general"])
        self.assertEqual(cfg["repos"], {})

    def test_later_file_overrides_scalars_and_merges_repo_tables(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "user.toml"
            repo = Path(tmp) / "repo.toml"
            user.write_text(
                '[general]\n'
                'tld = "test"\n'
                'php_fpm_socket = "/tmp/php.sock"\n'
                '\n'
                '[repos.myapp]\n'
                'source = "/code/myapp"\n'
                'env_keys = ["APP_URL"]\n'
            )
            repo.write_text(
                '[general]\n'
                'tld = "localhost"\n'
                '\n'
                '[repos.myapp]\n'
                'skip_test_db = true\n'
            )
            with isolated_env():
                cfg = config.load_config(paths=[user, repo])
        self.assertEqual(cfg["general"]["tld"], "localhost")
        self.assertEqual(cfg["general"]["php_fpm_socket"], "/tmp/php.sock")
        self.assertEqual(cfg["general"]["caddyfile"], config.DEFAULTS["general"]["caddyfile"])
        self.assertEqual(cfg["repos"]["myapp"]["source"], "/code/myapp")
        self.assertEqual(cfg["repos"]["myapp"]["env_keys"], ["APP_URL"])
        self.assertTrue(cfg["repos"]["myapp"]["skip_test_db"])

    def test_environment_overrides_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            user = Path(tmp) / "user.toml"
            user.write_text('[general]\ntld = "test"\ncaddy_sites_dir = "/from/file"\n')
            with isolated_env(WORKTREE_KIT_TLD="example", WORKTREE_KIT_SITES_DIR="/from/env"):
                cfg = config.load_config(paths=[user])
        self.assertEqual(cfg["general"]["tld"], "example")
        self.assertEqual(cfg["general"]["caddy_sites_dir"], "/from/env")


class MatchRepoTests(unittest.TestCase):
    def test_path_match_wins_over_remote_and_directory(self):
        found = config.match_repo(
            {"repos": {
                "myapp": {"remote_match": "example/myapp", "dir_match": "myapp"},
                "otherapp": {"path_match": r"/worktrees/otherapp-"},
            }},
            remote_url="https://github.com/example/myapp.git",
            dir_name="myapp",
            full_path="/worktrees/otherapp-feat",
        )
        self.assertEqual(found, "otherapp")

    def test_remote_match_beats_a_later_repos_directory(self):
        found = config.match_repo(
            {"repos": {
                "myapp": {"remote_match": "example/myapp"},
                "otherapp": {"dir_match": "checkout"},
            }},
            remote_url="https://github.com/example/myapp.git",
            dir_name="checkout",
            full_path="/tmp/checkout",
        )
        self.assertEqual(found, "myapp")

    def test_directory_match_is_an_unanchored_regex(self):
        found = config.match_repo(
            {"repos": {"myapp": {"remote_match": "example/myapp", "dir_match": "myapp"}}},
            remote_url="",
            dir_name="myapp-feat-auth",
            full_path="/code/myapp-feat-auth",
        )
        self.assertEqual(found, "myapp")

    def test_omitted_patterns_fall_back_to_the_repo_key(self):
        found = config.match_repo(
            {"repos": {"myapp": {"source": "~/code/myapp"}}},
            remote_url="https://github.com/example/myapp.git",
            dir_name="something-else",
            full_path="/tmp/something-else",
        )
        self.assertEqual(found, "myapp")

    def test_no_match_returns_none(self):
        found = config.match_repo(
            {"repos": {"myapp": {"dir_match": "^myapp$"}}},
            remote_url="https://github.com/example/other.git",
            dir_name="other",
            full_path="/tmp/other",
        )
        self.assertIsNone(found)

    def test_earlier_directory_match_shadows_a_later_remote(self):
        found = config.match_repo(
            {"repos": {
                "first": {"dir_match": "work"},
                "second": {"remote_match": "example/second"},
            }},
            remote_url="https://github.com/example/second.git",
            dir_name="work",
            full_path="/tmp/work",
        )
        self.assertEqual(found, "first")


class ExampleFileTests(unittest.TestCase):
    def test_example_general_section_matches_defaults(self):
        example = Path(__file__).resolve().parents[1] / "worktree-kit.toml.example"
        with example.open("rb") as handle:
            data = tomllib.load(handle)
        self.assertEqual(data["general"], config.DEFAULTS["general"])
        allowed = {
            "source", "path_match", "remote_match", "dir_match", "env_keys",
            "skip_test_db", "domain", "link_subdomain", "links",
        }
        self.assertIn("myapp", data["repos"])
        self.assertLessEqual(set(data["repos"]["myapp"]), allowed)
        self.assertIn("source", data["repos"]["myapp"])


if __name__ == "__main__":
    unittest.main()
