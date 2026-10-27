"""Caddy + PHP-FPM integration.

Every worktree is served by one shared PHP-FPM pool behind one Caddy instance.
Each worktree gets a small Caddy site file that terminates TLS for its domain
(and every subdomain beneath it), roots it at the worktree's public/ directory,
and hands PHP requests to the pool's socket.

There is no per-worktree process: an idle worktree costs nothing, and bringing
one up or down is writing or deleting a site file and reloading Caddy.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any

from .config import DEFAULTS


def _general(config: dict[str, Any], key: str) -> str:
    """Return one [general] setting, falling back to the built-in default."""
    return config.get("general", {}).get(key, DEFAULTS["general"][key])


def detect_worktree_dir(cwd: Path | None = None) -> str:
    """Return the worktree's directory name, which is also its site name.

    Paseo names the worktree directory, so that name is already unique.
    """
    return (cwd or Path.cwd()).name


def worktree_domain(
    worktree_dir: str,
    repo_conf: dict[str, Any],
    tld: str,
) -> str:
    """Return the domain a worktree is served at.

    Nested under the repo's own domain when one is configured, so a project's
    worktrees group together (`feat-auth.myapp.test`) rather than sharing
    a flat namespace. A tenant subdomain then sits one label further out
    (`acme.feat-auth.myapp.test`), which a single wildcard still covers.

    Falls back to `{worktree}.{tld}` for repos with no base domain.
    """
    base = repo_conf.get("domain")
    if not base:
        return f"{worktree_dir}.{tld}"

    # The source checkout itself is served at the base domain rather than at
    # a redundant `myapp.myapp.test`.
    if worktree_dir == base.split(".")[0]:
        return base

    return f"{worktree_dir}.{base}"


def write_workspace_links(
    domain: str,
    repo_conf: dict[str, Any],
    path: str | Path = "workspace-links.json",
) -> Path | None:
    """Write Paseo's workspace-links.json so the worktree's app is one click away.

    Paseo reads this from the workspace root and shows each entry as a header
    button. Returns None when the repo configures no links.

    The default entry points at the repo's `link_subdomain` (typically a host
    the app reserves so it skips tenant resolution) because the bare worktree
    domain 404s on an app that resolves a tenant from the subdomain.
    """
    links = repo_conf.get("links")

    if links is None:
        subdomain = repo_conf.get("link_subdomain")
        host = f"{subdomain}.{domain}" if subdomain else domain
        links = [{"label": "App", "url": f"https://{host}"}]

    if not links:
        return None

    # A configured link may template the domain rather than hardcode a host.
    resolved = [
        {**link, "url": link["url"].format(domain=domain)}
        for link in links
        if link.get("url")
    ]

    target = Path(path)
    target.write_text(json.dumps(resolved, indent=2) + "\n")
    return target


def sites_dir(config: dict[str, Any]) -> Path:
    """Directory holding one Caddy site file per worktree."""
    path = Path(os.path.expanduser(_general(config, "caddy_sites_dir")))
    path.mkdir(parents=True, exist_ok=True)
    return path


def site_file(config: dict[str, Any], worktree_dir: str) -> Path:
    return sites_dir(config) / f"{worktree_dir}.caddy"


def fpm_socket(config: dict[str, Any]) -> Path:
    """The PHP-FPM socket every worktree's site hands PHP requests to."""
    return Path(os.path.expanduser(_general(config, "php_fpm_socket")))


def write_site(
    config: dict[str, Any],
    worktree_dir: str,
    domain: str,
    root: Path,
    entry_subdomain: str | None = None,
) -> Path:
    """Write the Caddy site file for this worktree.

    The wildcard covers one label, which is what a subdomain-per-tenant app
    needs: acme.<worktree>.test resolves, and Caddy issues a matching cert from
    its own CA. Both names are listed because a wildcard does not match the
    bare domain.

    *entry_subdomain* redirects the bare domain there. An app that reads the
    tenant from the first label sees `<worktree>.<repo>.test` as a request for
    a tenant named after the worktree, and 404s -- the bare domain is only
    meaningful when the app domain is two labels, as it is in production.
    Redirecting in the proxy keeps that quirk out of the app.

    php_fastcgi does Laravel's front-controller routing itself (existing file,
    then directory index, then index.php) and sets HTTPS for the TLS listener,
    so the app generates https URLs without trusting any proxy headers.
    """
    path = site_file(config, worktree_dir)

    redirect = ""
    if entry_subdomain:
        redirect = (
            f"\n{domain} {{\n"
            f"\ttls internal\n"
            f"\tredir https://{entry_subdomain}.{domain}{{uri}}\n"
            f"}}\n"
        )
        hosts = f"*.{domain}"
    else:
        hosts = f"*.{domain}, {domain}"

    path.write_text(
        f"# worktree-kit: {worktree_dir}\n"
        f"{hosts} {{\n"
        f"\ttls internal\n"
        f"\troot * \"{root}\"\n"
        f"\tphp_fastcgi unix/{fpm_socket(config)}\n"
        f"\tfile_server\n"
        f"}}\n"
        f"{redirect}"
    )
    return path


def reload(config: dict[str, Any]) -> bool:
    """Ask the running Caddy to pick up the site files. True if it reloaded.

    Uses the admin API rather than a service restart: it needs no privileges
    and leaves every other worktree's connections untouched.
    """
    caddyfile = Path(os.path.expanduser(_general(config, "caddyfile")))
    if not caddyfile.is_file():
        print(f"  Warning: no Caddyfile at {caddyfile}; skipped reload")
        return False

    result = subprocess.run(
        ["caddy", "reload", "--config", str(caddyfile)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        print(f"  Warning: caddy reload failed: {result.stderr.strip()[:200]}")
        return False
    return True


def serve(
    config: dict[str, Any],
    worktree_dir: str,
    domain: str,
    repo_conf: dict[str, Any] | None = None,
    root: Path | None = None,
) -> bool:
    """Bring a worktree online: write its site file and reload Caddy.

    Returns whether Caddy accepted the reload. The site file is written either way.
    """
    entry = (repo_conf or {}).get("link_subdomain")
    public = (root or Path.cwd()) / "public"

    socket_path = fpm_socket(config)
    if not socket_path.exists():
        print(f"  Warning: no PHP-FPM socket at {socket_path}; is php-fpm running?")

    write_site(config, worktree_dir, domain, public, entry_subdomain=entry)
    reloaded = reload(config)
    if reloaded:
        print(f"  Serving https://{domain} (and *.{domain})")
        if entry:
            print(f"    bare domain redirects to https://{entry}.{domain}")
    else:
        print(f"  Wrote site file for {domain}; reload Caddy to activate")
    return reloaded


def unserve(config: dict[str, Any], worktree_dir: str, domain: str) -> None:
    """Take a worktree offline: drop its site and reload Caddy."""
    path = site_file(config, worktree_dir)
    if path.is_file():
        path.unlink()
        print(f"  Removed site: {domain}")
        reload(config)
