"""worktree-setup: full worktree setup orchestration."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

from . import caddy, config, db, deps, env


def ensure_repo(repo_name: str | None) -> str:
    """Return *repo_name*, or exit 1 when detection failed.

    A hook that treats exit 0 as "the worktree is ready" would otherwise
    continue after setup changed nothing.
    """
    if repo_name:
        return repo_name
    print("Warning: Could not detect this repository in the worktree-kit config. Nothing was changed.")
    raise SystemExit(1)


def clear_inherited_test_db(
    worktree_dir: str,
    repo_conf: dict,
    env_path: str | Path = ".env",
) -> bool:
    """Blank TEST_DB_DATABASE when it is not this worktree's derived name.

    A copied `.env` still names the source checkout's database. That value has
    to be gone before the slow install steps, because a failure there used to
    leave it for archive to drop. Returns True when a value was cleared.
    The derived name is written later, only after CREATE succeeds.
    """
    if repo_conf.get("skip_test_db", False):
        return False
    current = env.env_get("TEST_DB_DATABASE", env_path)
    if not current:
        return False
    expected = db.test_db_name(worktree_dir, repo_conf, env_path)
    if current == expected:
        return False
    env.env_set("TEST_DB_DATABASE", "", env_path)
    return True


def run_captured(cmd: list[str]) -> None:
    """Run *cmd*. Success stays quiet; a failure prints the command's output."""
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode == 0:
        return
    text = "\n".join(
        part.strip() for part in (result.stdout, result.stderr) if part and part.strip()
    )
    if text:
        print(text[-4000:])
    raise subprocess.CalledProcessError(result.returncode, cmd, result.stdout, result.stderr)


def npm_install_preserving_lock(run=None, root: Path | None = None) -> None:
    """npm install, putting package-lock.json back even when install fails.

    npm rewrites the lockfile as it works. Restoring the backup in a finally
    means a failed install cannot leave that rewrite behind. *root* is the
    directory that holds the lockfile; the npm process itself still runs in
    the current directory.
    """
    base = root or Path()
    lockfile = base / "package-lock.json"
    backup = base / "package-lock.json.backup"
    if lockfile.is_file():
        shutil.copy2(lockfile, backup)
    try:
        if run is None:
            run_captured(["npm", "install", "--silent"])
        else:
            run()
    finally:
        if backup.is_file():
            shutil.move(str(backup), str(lockfile))


def copy_claude_settings(source: Path, dest: Path | None = None) -> None:
    """Copy Claude Code's local settings into the worktree when the source has them.

    Claude Code writes "always allow" answers into `.claude/settings.local.json`.
    The file is copied so one worktree's approvals stay in that worktree. A
    symlink would write them back into the source checkout. An existing file
    is left as it is.
    """
    root = Path.cwd() if dest is None else dest
    local_settings = root / ".claude" / "settings.local.json"
    if local_settings.exists() or local_settings.is_symlink():
        print("  Keeping existing .claude/settings.local.json")
        return

    source_settings = source / ".claude" / "settings.local.json"
    if not source_settings.is_file():
        return

    local_settings.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_settings, local_settings)
    print(f"  Copied .claude/settings.local.json from: {source}")


def run(*, fresh: bool = False) -> None:
    """Set up a Laravel worktree: copy .env, install deps, create test DB, serve it.

    With *fresh*, vendor/ and node_modules/ are removed first so they are
    re-provisioned by cloning. That is how an older worktree, which owns a full
    private copy of each, gives that disk back.
    """
    print("Setting up Laravel worktree...")

    cfg = config.load_config()
    repo_name = ensure_repo(config.detect_repo(cfg))
    repo_conf = config.get_repo_config(cfg, repo_name)
    tld = cfg["general"]["tld"]
    worktree_dir = caddy.detect_worktree_dir()

    # 1. Seed .env from the source repo, but never over an existing one.
    #
    # Re-running setup on a live worktree is a normal thing to do (to move it
    # onto a new serving setup, or after changing config), and overwriting its
    # .env would silently discard whatever the branch needed -- a key being
    # tested, a service pointed somewhere else. Only the domain and prefix
    # keys are rewritten below, so an existing file keeps everything else.
    source = config.source_path(cfg, repo_name)
    if Path(".env").is_file():
        print("  Keeping existing .env (updating worktree keys only)")
    elif source and (source / ".env").is_file():
        shutil.copy2(source / ".env", ".env")
        print(f"  Copied .env from: {source}")
    else:
        print("  Warning: No .env file found in source repository")

    # The copied file still names the source checkout's test database. Blank
    # that before composer, npm, or the asset build can fail and leave it.
    if Path(".env").is_file() and clear_inherited_test_db(worktree_dir, repo_conf):
        print("  Cleared inherited TEST_DB_DATABASE")

    # 2. Update .env for worktree domain
    if Path(".env").is_file():
        env.update_worktree_env(worktree_dir, repo_conf, tld)

    # 2b. Point paseo.json at the source checkout's copy.
    #
    # Paseo snapshots paseo.json into each new worktree. When the file is
    # gitignored it is the only copy that propagates, so every worktree freezes
    # whatever the config looked like that day and later edits never reach them.
    # A symlink collapses them onto one file. Paseo only reads paseo.json
    # (script ls/start/stop), so nothing writes back through the link.
    #
    # No-op for anyone not using Paseo: it requires an existing symlink target.
    if source:
        source_paseo = source / "paseo.json"
        local_paseo = Path("paseo.json")
        if source_paseo.is_file() and not local_paseo.is_symlink():
            if local_paseo.is_file():
                local_paseo.unlink()
            local_paseo.symlink_to(source_paseo)
            print(f"  Linked paseo.json to: {source_paseo}")

        # Claude Code writes "always allow" answers into this file. Copy it so
        # one worktree's approvals stay here; a symlink would store them back
        # in the source checkout.
        copy_claude_settings(source)

    # 3. Install composer dependencies
    reclaimed = 0
    if Path("composer.json").is_file():
        # With --fresh the existing directory is moved aside rather than
        # deleted, so a clone or install that fails can be rolled back instead
        # of leaving the worktree with no dependencies at all.
        stash = deps.stash_dir("vendor") if fresh else None
        try:
            if deps.clone_dir(
                source, "vendor", "composer.lock",
                verify=deps.declared_packages("composer.json", "require", "require-dev"),
            ):
                print("  Cloned vendor/ from source (composer.lock matches)")
            else:
                print("  Installing composer dependencies...")
                run_captured(["composer", "install", "--no-interaction", "--quiet"])
                print("  Installed composer dependencies")
        except Exception:
            if deps.restore_stash(stash, "vendor"):
                print("  Restored the previous vendor/ after a failed refresh")
            raise
        reclaimed += deps.drop_stash(stash)

    # 4. Install npm dependencies (preserve lockfile)
    if Path("package.json").is_file():
        stash = deps.stash_dir("node_modules") if fresh else None
        try:
            if deps.clone_dir(
                source, "node_modules", "package-lock.json",
                verify=deps.declared_packages("package.json", "dependencies", "devDependencies"),
            ):
                print("  Cloned node_modules/ from source (package-lock.json matches)")
            else:
                print("  Installing npm dependencies...")
                npm_install_preserving_lock()
                print("  Installed npm dependencies")
        except Exception:
            if deps.restore_stash(stash, "node_modules"):
                print("  Restored the previous node_modules/ after a failed refresh")
            raise
        reclaimed += deps.drop_stash(stash)

    if reclaimed:
        print(f"  Reclaimed {reclaimed / 1024 ** 3:.2f} GB of duplicated dependencies")

    # 5. Build assets
    #
    # An existing build is kept when it is newer than everything that feeds
    # it: re-running setup on a live worktree (or one copied from another
    # machine with its build) shouldn't pay a full Vite build, but a build
    # older than the branch's own assets would 500 on a missing manifest
    # entry. --fresh always rebuilds.
    if Path("package.json").is_file() and deps.build_is_current() and not fresh:
        print("  Keeping existing public/build (--fresh to rebuild)")
    elif Path("package.json").is_file():
        print("  Building assets...")
        build = subprocess.run(["npm", "run", "build"], capture_output=True, text=True)

        # A cloned node_modules can carry a native module that macOS then
        # refuses to load (npm/cli#4828 leaves the wrong optional dependency in
        # place, and the copy fails code-signature validation). The clone looks
        # complete but cannot build, so fall back to a real install once.
        if build.returncode != 0 and deps.looks_like_native_module_failure(build.stderr):
            print("  Build failed on a cloned native module; reinstalling npm dependencies...")
            shutil.rmtree("node_modules", ignore_errors=True)
            npm_install_preserving_lock()
            build = subprocess.run(["npm", "run", "build"], capture_output=True, text=True)

        if build.returncode != 0:
            tail = (build.stderr or build.stdout).strip()[-400:]
            raise RuntimeError(f"npm run build failed:\n{tail}")
        print("  Built assets")

    # 5a. Drop Laravel's compiled caches.
    #
    # bootstrap/cache/packages.php records the packages discovered at the last
    # install. Cloning vendor/ does not rewrite it, so a worktree whose branch
    # once had a package still lists its service provider and the app dies at
    # boot with "Class ... not found". config.php and routes.php go stale the
    # same way once .env is rewritten for the worktree's own domain.
    if Path("artisan").is_file():
        for cached in Path("bootstrap/cache").glob("*.php"):
            cached.unlink()

    # 5b. Publish package assets into public/.
    #
    # Filament and its plugins serve CSS and JS from public/css and public/js,
    # which are gitignored and so absent from a fresh worktree. Without them
    # every admin page 404s its own stylesheets and scripts, which looks like
    # broken styling rather than a missing setup step.
    if Path("artisan").is_file() and Path("vendor/filament").is_dir():
        print("  Publishing Filament assets...")
        result = subprocess.run(
            ["php", "artisan", "filament:assets"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            print("  Published Filament assets")
        else:
            print(f"  Warning: filament:assets failed: {result.stderr.strip()[:200]}")

    # 6. Generate test DB name + create DB (skip if repo uses SQLite tests etc.)
    if Path(".env").is_file() and not repo_conf.get("skip_test_db", False):
        db_name = db.test_db_name(worktree_dir, repo_conf)
        print(f"  Test database name: {db_name}")
        # Create first, record second. Writing TEST_DB_DATABASE for a database
        # that was never created leaves .env pointing at nothing, and the
        # failure resurfaces much later as an unexplained test error.
        if not db.create_test_db(db_name):
            # CREATE did not happen. Leave no name behind, including one that
            # was written back during the install steps.
            if env.env_get("TEST_DB_DATABASE"):
                env.env_set("TEST_DB_DATABASE", "")
            raise RuntimeError(
                f"Could not create test database {db_name!r}. "
                "TEST_DB_DATABASE was left unset; re-run worktree-setup once "
                "the cause above is resolved."
            )
        env.env_set("TEST_DB_DATABASE", db_name)
    elif repo_conf.get("skip_test_db", False):
        print("  Skipping test database (disabled for this repo)")

    # 7. Serve the worktree from the shared PHP-FPM pool behind Caddy
    print("  Registering site...")
    domain = caddy.worktree_domain(worktree_dir, repo_conf, tld)
    caddy.serve(cfg, worktree_dir, domain, repo_conf=repo_conf)

    # 8. Point Paseo's workspace link at this worktree's app
    if links := caddy.write_workspace_links(domain, repo_conf):
        print(f"  Wrote {links.name}")

    print("Worktree setup complete!")
