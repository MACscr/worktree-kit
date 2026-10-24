"""Remove per-site files left behind by Laravel Herd.

Herd registers each site as a symlink, an nginx config, and certificate files
under its own config directory (Herd kept Valet's layout). Once Caddy is
serving worktrees, those files are unused. This command deletes only those
generated files. Trusted copies in the login keychain are left in place; the
command prints how to remove them. Deleting a symlink does not delete the
worktree it pointed at, and Herd's PHP, MySQL, and Redis are not touched.
"""

from __future__ import annotations

import os
from pathlib import Path

from .config import load_config

VALET_DIR = "~/Library/Application Support/Herd/config/valet"
CERT_SUFFIXES = ("crt", "key", "csr", "conf")


def valet_dir() -> Path:
    return Path(os.path.expanduser(VALET_DIR))


def _site_names(root: Path) -> list[str]:
    sites = root / "Sites"
    if not sites.is_dir():
        return []
    # Includes broken links, whose targets are already gone: those are exactly
    # the leftovers worth clearing, so is_symlink rather than exists.
    return sorted(p.name for p in sites.iterdir() if p.is_symlink())


def run(*, apply: bool = False, dead_only: bool = False) -> None:
    """Report (and with apply=True, remove) Herd's per-site artifacts."""
    root = valet_dir()
    if not root.is_dir():
        print(f"No Herd config at {root}; nothing to purge.")
        return

    tld = load_config().get("general", {}).get("tld", "test")
    names = _site_names(root)
    if not names:
        print("No Herd sites found; nothing to purge.")
        return

    targets: list[str] = []
    for name in names:
        link = root / "Sites" / name
        # A link whose target is gone belongs to an archived worktree.
        if dead_only and link.exists():
            continue
        targets.append(name)

    if not targets:
        print(f"No {'dead ' if dead_only else ''}sites to purge.")
        return

    files = 0
    for name in targets:
        domain = f"{name}.{tld}"
        if (root / "Sites" / name).is_symlink():
            files += 1
        if (root / "Nginx" / domain).is_file():
            files += 1
        files += sum(
            1 for ext in CERT_SUFFIXES
            if (root / "Certificates" / f"{domain}.{ext}").is_file()
        )

    scope = "dead" if dead_only else "all"
    print(f"{len(targets)} {scope} Herd site(s), {files} generated file(s).")

    if not apply:
        print("\nDry run. Nothing was deleted. Re-run with --apply to remove them.")
        print("Sites (first 10):")
        for name in targets[:10]:
            state = "" if (root / "Sites" / name).exists() else "  [target gone]"
            print(f"  {name}.{tld}{state}")
        if len(targets) > 10:
            print(f"  ... and {len(targets) - 10} more")
        return

    removed = 0
    for name in targets:
        domain = f"{name}.{tld}"

        link = root / "Sites" / name
        # Re-checked at deletion time, not just during the count: this must
        # never remove a real directory if something unexpected is in Sites/.
        if link.is_symlink():
            link.unlink()
            removed += 1

        nginx_conf = root / "Nginx" / domain
        if nginx_conf.is_file():
            nginx_conf.unlink()
            removed += 1

        for ext in CERT_SUFFIXES:
            cert = root / "Certificates" / f"{domain}.{ext}"
            if cert.is_file():
                cert.unlink()
                removed += 1

    print(f"Removed {removed} file(s) for {len(targets)} site(s).")
    print(
        "\nTrusted certificates remain in the login keychain. They are inert "
        "once the sites are gone; to remove them:\n"
        '  security delete-certificate -c "<domain>" ~/Library/Keychains/login.keychain-db'
    )
