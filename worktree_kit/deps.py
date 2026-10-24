"""Dependency directory cloning.

Installing dependencies is the slowest part of setting up a worktree, and
almost every worktree wants exactly what the source checkout already has.
APFS can clone a directory copy-on-write: the copy is independent, but shares
storage with the original until a file is written.

That makes `vendor/` and `node_modules/` nearly free to duplicate — seconds
instead of a full install, and no extra disk until something diverges. Unlike
a symlink, the clone is a real directory: `composer require` in one worktree
un-shares only the files it touches and leaves every other worktree alone.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


def _same_lockfile(source: Path, lockfile: str) -> bool:
    """True when this worktree's lockfile is byte-identical to the source's.

    A branch that changed dependencies must not inherit the source's packages,
    so the clone is only safe when the locks agree. Comparing the lock rather
    than the manifest is deliberate: composer.json can differ in ways that
    don't affect installed packages, but the lock is the resolved truth.
    """
    local = Path(lockfile)
    remote = source / lockfile

    # No lockfile on either side means there is nothing to verify against, so
    # fall back to a real install rather than guess.
    if not local.is_file() or not remote.is_file():
        return False

    return local.read_bytes() == remote.read_bytes()


NATIVE_MODULE_MARKERS = (
    "library load denied by system policy",
    "code signature in",
    "Cannot find module @rollup/rollup-",
    "npm has a bug related to optional dependencies",
)


def looks_like_native_module_failure(output: str) -> bool:
    """True when a build failed because a cloned native module won't load.

    npm resolves platform-specific optional dependencies at install time, and
    a copied node_modules can end up with a binary macOS refuses to dlopen
    (code-signature validation fails for a copy). The fix is a real install,
    not a retry, so the caller needs to tell this apart from a genuine build
    error in the app's own code.
    """
    return any(marker in (output or "") for marker in NATIVE_MODULE_MARKERS)


def declared_packages(manifest: str, *sections: str) -> tuple[str, ...]:
    """Return the package directories *manifest* says should be installed.

    Used to spot-check a source directory before cloning it. Composer platform
    requirements (`php`, `ext-*`) name no directory, so a composer.json skips
    any requirement without a slash. npm package names are directories either
    way (`vite`, `@scope/pkg`), so a package.json keeps them all. A missing
    path makes cloning fall back to a real install.
    """
    path = Path(manifest)
    if not path.is_file():
        return ()

    try:
        data = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return ()

    composer = path.name == "composer.json"
    names: list[str] = []
    for section in sections:
        for name in (data.get(section) or {}):
            if name.startswith("ext-"):
                continue
            if composer and "/" not in name:
                continue
            names.append(name)

    return tuple(sorted(names))


def supports_clone(path: Path) -> bool:
    """True when *path* is on a filesystem that can clone (APFS).

    Determined by cloning a temporary file rather than inspecting the volume:
    `diskutil info` takes a device, not a path, and mapping an arbitrary
    directory back to its volume is more work than simply trying it.
    """
    probe = path / f".wt-clone-probe-{os.getpid()}"
    copy = Path(f"{probe}.copy")
    try:
        probe.write_bytes(b"clone probe")
        result = subprocess.run(
            ["cp", "-c", str(probe), str(copy)],
            capture_output=True,
            text=True,
        )
        return result.returncode == 0
    except OSError:
        return False
    finally:
        probe.unlink(missing_ok=True)
        copy.unlink(missing_ok=True)


def stash_dir(name: str) -> Path | None:
    """Move *name* aside so a clone can replace it, keeping it recoverable.

    A worktree set up before dependency cloning owns a full private copy of
    vendor/ and node_modules/ -- several hundred MB each that a clone would
    have shared with the source checkout. Replacing them is what reclaims the
    disk, but the replacement can fail for reasons this tool does not control:
    a corrupt composer cache, or npm's optional-dependency bug leaving a native
    module macOS then refuses to load. Deleting first turns any of those into a
    worktree with no dependencies at all.

    So the old directory is renamed rather than removed, and the caller commits
    (`drop_stash`) or rolls back (`restore_stash`) once it knows the outcome.
    """
    target = Path(name)
    if not target.is_dir() or target.is_symlink():
        return None

    stash = target.with_name(f".{target.name}.wt-old")
    if stash.exists():
        shutil.rmtree(stash, ignore_errors=True)

    try:
        target.rename(stash)
    except OSError:
        return None
    return stash


def restore_stash(stash: Path | None, name: str) -> bool:
    """Put a stashed directory back. True if it was restored."""
    if stash is None or not stash.is_dir():
        return False

    target = Path(name)
    if target.exists():
        shutil.rmtree(target, ignore_errors=True)

    try:
        stash.rename(target)
    except OSError:
        return False
    return True


def drop_stash(stash: Path | None) -> int:
    """Delete a stashed directory for good. Returns bytes freed."""
    if stash is None or not stash.is_dir():
        return 0

    freed = sum(f.stat().st_size for f in stash.rglob("*") if f.is_file())
    shutil.rmtree(stash, ignore_errors=True)
    return freed


def clone_dir(
    source: Path | None,
    name: str,
    lockfile: str,
    verify: tuple[str, ...] = (),
) -> bool:
    """Clone *name* from *source* into the current worktree.

    Returns False when the clone was skipped for any reason, leaving the
    caller to run a normal install. Never raises on a failed clone: a slow
    setup is better than a failed one.

    *verify* names paths that must exist inside the source directory. A
    matching lockfile only proves the source resolved the same dependency
    versions, not that its directory is complete — a source checkout whose
    install predates a newly added package looks current by its lock but is
    missing that package on disk. Cloning it would propagate the gap to every
    new worktree, so a spot check falls back to a real install instead.
    """
    if source is None:
        return False

    origin = source / name
    if not origin.is_dir():
        return False

    if not _same_lockfile(source, lockfile):
        return False

    if any(not (origin / rel).exists() for rel in verify):
        return False

    target = Path(name)
    if target.exists() or target.is_symlink():
        return False

    if not supports_clone(source):
        return False

    # -c requests a clone and fails outright rather than silently falling back
    # to a full copy, so a non-APFS target is caught here too.
    result = subprocess.run(
        ["cp", "-Rc", str(origin), str(target)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        # A partial copy would look like a complete install to everything
        # downstream, so clear it before handing back to the installer.
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        return False

    return True


def build_is_current(root: Path | str = ".") -> bool:
    """True if public/build exists and is newer than every input to the Vite build."""
    root = Path(root)
    manifest = next((m for m in (root / "public/build/manifest.json",
                                 root / "public/build/.vite/manifest.json") if m.is_file()), None)
    if manifest is None:
        return False
    built_at = manifest.stat().st_mtime
    inputs = [root / "package.json", root / "package-lock.json", *root.glob("vite.config.*")]
    resources = root / "resources"
    if resources.is_dir():
        inputs += [p for p in resources.rglob("*") if p.is_file()]
    return all(p.stat().st_mtime <= built_at for p in inputs if p.exists())
