"""Claude Code settings copy. Uses a temp directory, not a real checkout."""

from __future__ import annotations

import io
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worktree_kit import env
from worktree_kit.setup import (
    clear_inherited_test_db,
    copy_claude_settings,
    ensure_repo,
    npm_install_preserving_lock,
    run_captured,
)


class ClaudeSettingsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        root = Path(self.tmp.name)
        self.source = root / "source"
        self.dest = root / "worktree"
        self.source.mkdir()
        self.dest.mkdir()

    def write_source(self, text: str = '{"permissions": ["allow"]}\n') -> Path:
        path = self.source / ".claude" / "settings.local.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def local(self) -> Path:
        return self.dest / ".claude" / "settings.local.json"

    def test_copies_a_real_file_and_leaves_the_source_alone(self):
        source_file = self.write_source()
        buf = io.StringIO()
        with redirect_stdout(buf):
            copy_claude_settings(self.source, self.dest)
        self.assertEqual(
            buf.getvalue(),
            f"  Copied .claude/settings.local.json from: {self.source}\n",
        )
        copied = self.local()
        self.assertTrue(copied.is_file())
        self.assertFalse(copied.is_symlink())
        self.assertEqual(copied.read_text(), source_file.read_text())
        copied.write_text("changed-here\n")
        self.assertEqual(source_file.read_text(), '{"permissions": ["allow"]}\n')

    def test_keeps_an_existing_file(self):
        self.write_source("from-source\n")
        self.local().parent.mkdir(parents=True)
        self.local().write_text("already-here\n")
        buf = io.StringIO()
        with redirect_stdout(buf):
            copy_claude_settings(self.source, self.dest)
        self.assertEqual(buf.getvalue(), "  Keeping existing .claude/settings.local.json\n")
        self.assertEqual(self.local().read_text(), "already-here\n")

    def test_keeps_an_existing_symlink(self):
        source_file = self.write_source()
        self.local().parent.mkdir(parents=True)
        self.local().symlink_to(source_file)
        buf = io.StringIO()
        with redirect_stdout(buf):
            copy_claude_settings(self.source, self.dest)
        self.assertEqual(buf.getvalue(), "  Keeping existing .claude/settings.local.json\n")
        self.assertTrue(self.local().is_symlink())

    def test_noop_when_the_source_has_no_settings(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            copy_claude_settings(self.source, self.dest)
        self.assertEqual(buf.getvalue(), "")
        self.assertFalse((self.dest / ".claude").exists())


class InheritedDatabaseTests(unittest.TestCase):
    def test_clears_a_name_copied_from_the_source_checkout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("DB_DATABASE=appdb\nTEST_DB_DATABASE=appdb_pest\n")
            self.assertTrue(clear_inherited_test_db("myapp-feat", {"dir_match": "myapp"}, path))
            self.assertEqual(env.env_get("TEST_DB_DATABASE", path), "")

    def test_keeps_the_name_derived_for_this_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("DB_DATABASE=appdb\nTEST_DB_DATABASE=appdb_pest_feat\n")
            self.assertFalse(clear_inherited_test_db("myapp-feat", {"dir_match": "myapp"}, path))
            self.assertEqual(env.env_get("TEST_DB_DATABASE", path), "appdb_pest_feat")


class NpmLockTests(unittest.TestCase):
    def test_a_failed_install_restores_the_lockfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "package-lock.json"
            lock.write_text("original\n")

            def fail() -> None:
                lock.write_text("rewritten by npm\n")
                raise RuntimeError("npm failed")

            with self.assertRaises(RuntimeError):
                npm_install_preserving_lock(run=fail, root=root)
            self.assertEqual(lock.read_text(), "original\n")
            self.assertFalse((root / "package-lock.json.backup").exists())

    def test_a_successful_install_restores_the_lockfile(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lock = root / "package-lock.json"
            lock.write_text("original\n")

            def succeed() -> None:
                lock.write_text("rewritten by npm\n")

            npm_install_preserving_lock(run=succeed, root=root)
            self.assertEqual(lock.read_text(), "original\n")


class CommandOutputTests(unittest.TestCase):
    def test_failure_output_is_shown(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(subprocess.CalledProcessError):
                run_captured([
                    sys.executable, "-c",
                    "import sys; sys.stderr.write('visible failure\\n'); raise SystemExit(3)",
                ])
        self.assertIn("visible failure", buf.getvalue())

    def test_success_stays_quiet(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            run_captured([sys.executable, "-c", "print('hidden')"])
        self.assertEqual(buf.getvalue(), "")


class RepoDetectionTests(unittest.TestCase):
    def test_an_undetected_repo_exits_nonzero(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            with self.assertRaises(SystemExit) as caught:
                ensure_repo(None)
        self.assertEqual(caught.exception.code, 1)
        self.assertIn("Nothing was changed", buf.getvalue())

    def test_a_detected_repo_is_returned(self):
        self.assertEqual(ensure_repo("myapp"), "myapp")


if __name__ == "__main__":
    unittest.main()
