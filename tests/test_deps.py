"""Dependency manifest checks and the Vite freshness rule."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from worktree_kit import deps


class DeclaredPackagesTests(unittest.TestCase):
    def test_composer_skips_platform_requirements(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "composer.json"
            path.write_text(
                '{"require": {"php": "^8.3", "ext-pdo": "*", "laravel/framework": "^12.0"},'
                ' "require-dev": {"pestphp/pest": "^3.0"}}'
            )
            self.assertEqual(
                deps.declared_packages(str(path), "require", "require-dev"),
                ("laravel/framework", "pestphp/pest"),
            )

    def test_npm_keeps_unscoped_packages(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "package.json"
            path.write_text(
                '{"dependencies": {"vite": "^6.0", "@vitejs/plugin-vue": "^5.0"},'
                ' "devDependencies": {"axios": "^1.0"}}'
            )
            self.assertEqual(
                deps.declared_packages(str(path), "dependencies", "devDependencies"),
                ("@vitejs/plugin-vue", "axios", "vite"),
            )

    def test_missing_or_broken_manifest_is_empty(self):
        self.assertEqual(deps.declared_packages("/no/such/composer.json", "require"), ())
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "composer.json"
            path.write_text("{")
            self.assertEqual(deps.declared_packages(str(path), "require"), ())


class NativeModuleTests(unittest.TestCase):
    def test_recognizes_a_cloned_binary_failure(self):
        self.assertTrue(deps.looks_like_native_module_failure("code signature in <built>"))
        self.assertFalse(deps.looks_like_native_module_failure("Syntax error in the application"))
        self.assertFalse(deps.looks_like_native_module_failure(""))


class BuildFreshnessTests(unittest.TestCase):
    def test_manifest_newer_than_inputs_is_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            package = root / "package.json"
            package.write_text("{}\n")
            asset = root / "resources" / "app.js"
            asset.parent.mkdir()
            asset.write_text("x\n")
            manifest = root / "public" / "build" / "manifest.json"
            manifest.parent.mkdir(parents=True)
            manifest.write_text("{}\n")
            os.utime(package, (1, 1_000))
            os.utime(asset, (1, 1_000))
            os.utime(manifest, (1, 2_000))
            self.assertTrue(deps.build_is_current(root))
            os.utime(asset, (1, 3_000))
            self.assertFalse(deps.build_is_current(root))

    def test_missing_manifest_is_not_current(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertFalse(deps.build_is_current(tmp))


if __name__ == "__main__":
    unittest.main()
