"""Test-database name derivation. Does not talk to MySQL."""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worktree_kit import db


class SanitizeTests(unittest.TestCase):
    def test_strips_the_repo_name_and_normalizes(self):
        self.assertEqual(
            db._sanitize_suffix("myapp-feat-auth", {"dir_match": "myapp"}),
            "feat_auth",
        )

    def test_source_checkout_has_an_empty_suffix(self):
        self.assertEqual(db._sanitize_suffix("myapp", {"dir_match": "myapp"}), "")

    def test_leading_separators_and_case(self):
        self.assertEqual(db._sanitize_suffix("--Feat-Name", {}), "feat_name")


class FitTests(unittest.TestCase):
    def test_names_within_the_limit_are_unchanged(self):
        name = "a" * db.MAX_TEST_DB_NAME_LEN
        self.assertEqual(db._fit_to_limit(name, "suffix"), name)
        self.assertEqual(db._fit_to_limit("appdb_pest_feat", "feat"), "appdb_pest_feat")

    def test_long_names_stay_in_range_and_do_not_collapse(self):
        suffix_a = "feature_" + ("x" * 40) + "_one"
        suffix_b = "feature_" + ("x" * 40) + "_two"
        name_a = "appdb_pest_" + suffix_a
        name_b = "appdb_pest_" + suffix_b
        self.assertGreater(len(name_a), db.MAX_TEST_DB_NAME_LEN)
        fitted_a = db._fit_to_limit(name_a, suffix_a)
        fitted_b = db._fit_to_limit(name_b, suffix_b)
        self.assertLessEqual(len(fitted_a), db.MAX_TEST_DB_NAME_LEN)
        self.assertLessEqual(len(fitted_b), db.MAX_TEST_DB_NAME_LEN)
        self.assertNotEqual(fitted_a, fitted_b)
        self.assertEqual(fitted_a, db._fit_to_limit(name_a, suffix_a))


class TestDbNameTests(unittest.TestCase):
    def test_master_and_feature_names(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("DB_DATABASE=appdb\n")
            repo = {"dir_match": "myapp"}
            self.assertEqual(db.test_db_name("myapp", repo, path), "appdb_pest")
            self.assertEqual(
                db.test_db_name("myapp-feat-auth", repo, path),
                "appdb_pest_feat_auth",
            )

    def test_missing_database_key_defaults_to_laravel(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.env"
            self.assertEqual(
                db.test_db_name("myapp", {"dir_match": "myapp"}, missing),
                "laravel_pest",
            )

    def test_public_helper_shortens_a_long_branch(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("DB_DATABASE=appdb\n")
            long_dir = "myapp-" + ("branch_" * 12) + "alpha"
            name = db.test_db_name(long_dir, {"dir_match": "myapp"}, path)
        self.assertLessEqual(len(name), db.MAX_TEST_DB_NAME_LEN)
        self.assertTrue(name.startswith("appdb_pest_"))
        self.assertEqual(db.MAX_TEST_DB_NAME_LEN, 55)


class ArchiveNameTests(unittest.TestCase):
    def _env(self, text: str) -> Path:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        path = Path(directory.name) / ".env"
        path.write_text(text)
        return path

    def test_accepts_the_name_this_worktree_derives(self):
        path = self._env("DB_DATABASE=appdb\nTEST_DB_DATABASE=appdb_pest_feat\n")
        self.assertEqual(
            db.archive_test_db_name("myapp-feat", {"dir_match": "myapp"}, path),
            "appdb_pest_feat",
        )

    def test_refuses_the_source_checkouts_name(self):
        path = self._env("DB_DATABASE=appdb\nTEST_DB_DATABASE=appdb_pest\n")
        self.assertIsNone(db.archive_test_db_name("myapp-feat", {"dir_match": "myapp"}, path))

    def test_derives_a_name_when_nothing_is_recorded(self):
        path = self._env("DB_DATABASE=appdb\n")
        self.assertEqual(
            db.archive_test_db_name("myapp-feat", {"dir_match": "myapp"}, path),
            "appdb_pest_feat",
        )

    def test_refuses_the_application_database_and_unsafe_names(self):
        self.assertFalse(db.may_drop_test_db("appdb", "appdb", "appdb"))
        self.assertFalse(db.may_drop_test_db("app'db", "app'db", "other"))
        self.assertFalse(db.is_safe_identifier("appdb'; DROP DATABASE x"))
        self.assertFalse(db.is_safe_identifier("app`db"))
        self.assertIsNone(db.schemas_like_sql("app_db' OR '1'='1"))

    def test_like_escapes_wildcards(self):
        self.assertEqual(db.like_literal("app_db%"), r"app\_db\%")
        sql = db.schemas_like_sql("app_db")
        self.assertIsNotNone(sql)
        self.assertIn(r"app\_db%", sql)
        self.assertNotIn("LIKE 'app_db%", sql)


if __name__ == "__main__":
    unittest.main()
