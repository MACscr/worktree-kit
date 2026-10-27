# worktree-kit

Local HTTPS domains, MySQL test databases, and Redis prefixes for Laravel git worktrees.

Paseo creates the git worktree. Its hooks run `worktree-setup` when the workspace is created and `worktree-archive` when it is removed. Setup copies `.env` from the source checkout, clones `vendor/` and `node_modules/` when it can, creates a test database, and registers the worktree with Caddy. Archive drops that database, the worktree's Redis keys, and the Caddy site. One Caddy process and one PHP-FPM pool serve every worktree.

Python 3.11 or newer. The standard library is the only dependency. macOS, with Homebrew.

## Requirements

- Python 3.11+. macOS does not ship it. `brew install python@3.13` is enough.
- Homebrew `php@8.4`, with `php-fpm` running as your user (`brew services`).
- MySQL, with the `mysql` client on `PATH`. Redis, with `redis-cli` on `PATH`, when the app uses Redis.
- Caddy and dnsmasq, running as described under [Domains and Caddy](#domains-and-caddy).
- `composer` and `npm` on `PATH`. `cp -c` (APFS) for the fast dependency clone.

## Install

```bash
./install.sh
```

The script asks before overwriting `~/.worktree-kit.toml`. It writes that file and, if it is missing, `~/.worktree-kit/Caddyfile`. It adds `bin/` to `PATH` in `~/.zshrc`. It does not install packages or start services. When Python, the PHP-FPM socket, Caddy, or `/etc/resolver/test` is missing, it prints the machine setup.

`bin/worktree-setup` and `bin/worktree-archive` run the package from this checkout. `pip install` is optional; the scripts are the same commands.

## Configuration

`~/.worktree-kit.toml`, then `.worktree-kit.toml` in the worktree. `WORKTREE_KIT_SITES_DIR` and `WORKTREE_KIT_TLD` override both. See `worktree-kit.toml.example`.

`[general]`:

| Key | Default |
| --- | --- |
| `caddy_sites_dir` | `~/.worktree-kit/sites` |
| `caddyfile` | `~/.worktree-kit/Caddyfile` |
| `php_fpm_socket` | `/opt/homebrew/var/run/php-fpm.sock` |
| `tld` | `test` |

On Intel Homebrew the socket is `/usr/local/var/run/php-fpm.sock`.

`[repos.<name>]`. The name is a label. The first match wins: `path_match` across every repo, then file order of `remote_match`, then `dir_match`. An omitted pattern is the repo label. `remote_match` is applied to `git remote get-url origin`.

| Key | Meaning |
| --- | --- |
| `source` | Canonical checkout. `.env`, `vendor/`, `node_modules/`, `paseo.json`, and Claude settings are taken from here. |
| `path_match` | Regex against the worktree path. Checked first. |
| `remote_match` | Regex against the origin URL. |
| `dir_match` | Regex against the directory name. Also stripped when naming the test database. |
| `env_keys` | Which of `SESSION_DOMAIN`, `APP_URL`, `APP_DOMAIN` to rewrite. Only those three have a mapping. |
| `skip_test_db` | Skip MySQL test-database creation. Use this for SQLite or Postgres. |
| `domain` | Serve worktrees at `{directory}.{domain}`. The directory whose name is the first label of `domain` stays on `domain` itself. |
| `link_subdomain` | Redirect the bare domain to this host, and use it for the default Paseo link. |
| `links` | Paseo workspace links. `{domain}` in a URL is replaced. `[]` writes no `workspace-links.json`. |

If the directory does not match a repo, `worktree-setup` changes nothing and exits 1.

## Commands

Both run from the worktree root (the directory that contains `artisan` and `.env`).

### `worktree-setup`

`worktree-setup [--fresh]`

1. Copies `.env` from the source checkout when the worktree does not already have one.
2. Clears an inherited `TEST_DB_DATABASE` immediately, before installs, unless that value is already this worktree's derived name.
3. Rewrites domain keys that already exist and are listed in `env_keys`. Sets `SESSION_COOKIE` to `{directory}-session` when `SESSION_DOMAIN` exists. Sets `REDIS_PREFIX` to `{directory}:` when the app uses Redis, `CACHE_PREFIX` to `{directory}:cache:` when the cache store is redis or memcached, and `HORIZON_PREFIX` to `{directory}:horizon:` when `config/horizon.php` exists.
4. Symlinks `paseo.json` from the source checkout when the source has one and the local file is not already a symlink.
5. Copies `.claude/settings.local.json` from the source checkout when the worktree does not already have one.
6. Clones `vendor/` and `node_modules/`, or runs `composer install` / `npm install` when a clone is not safe. `--fresh` replaces an existing clone.
7. Builds frontend assets when the published build is older than the sources. Retries `npm install` once if a native module fails to load.
8. Deletes `bootstrap/cache/*.php`. Runs `php artisan filament:assets` when `vendor/filament` exists. A failure there is a warning; setup continues.
9. Creates the test database, then writes `TEST_DB_DATABASE`. If `CREATE` fails, the key is left empty and setup stops before the site is registered.
10. Writes the Caddy site file and reloads Caddy.
11. Writes `workspace-links.json`.

`composer install` and `npm install` are quiet when they succeed. On failure, the last part of their output is printed. `npm install` restores `package-lock.json` afterwards, including when it fails.

### `worktree-archive`

Drops the test database derived for this worktree, deletes Redis keys under its prefix, deletes the Caddy site file, and reloads Caddy. It does not delete the worktree directory. Paseo does that after the hook returns.

## Domains and Caddy

The worktree's name is its directory name. With `domain = "myapp.test"` and `tld = "test"`, directory `feat-auth` is `https://feat-auth.myapp.test`, and a tenant subdomain such as `acme.feat-auth.myapp.test` is covered by the same certificate. Directory `myapp` is served at `https://myapp.test`.

`~/.worktree-kit/Caddyfile` does not name sites. It turns on `local_certs`, sets the local CA intermediate lifetime to `8760h`, and imports `~/.worktree-kit/sites/*.caddy`. Setup writes one file per worktree:

```
*.feat-auth.myapp.test, feat-auth.myapp.test {
	tls internal
	root * "/path/to/worktree/public"
	php_fastcgi unix//opt/homebrew/var/run/php-fpm.sock
	file_server
}
```

An absolute socket produces the doubled slash (`unix/` plus `/opt/...`). `php_fastcgi` does Laravel's front-controller routing and sets `HTTPS`, so generated URLs are https. When `link_subdomain` is set, the wildcard block is the only one that runs PHP, and the bare domain redirects to that subdomain.

Caddy is a root LaunchDaemon, because binding port 443 needs root. It is not a Homebrew service. The process is:

```
caddy run --config ~/.worktree-kit/Caddyfile
```

`HOME` is your user, so the local CA lives in your keychain. `XDG_DATA_HOME` is `~/Library/Application Support`. Standard output and standard error go to `~/.worktree-kit/caddy.log`. `RunAtLoad` and `KeepAlive` are set. `caddy trust` once puts that CA in the system trust store. `install.sh` prints a plist with those keys.

`worktree-setup` and `worktree-archive` call `caddy reload --config ~/.worktree-kit/Caddyfile`. Reload uses the admin API, needs no extra privileges, and does not restart Caddy, so other worktrees keep their connections.

PHP-FPM is Homebrew `php@8.4`, started by `brew services` as your user (`~/Library/LaunchAgents/sh.brew.php@8.4.plist`). The pool listens on `/opt/homebrew/var/run/php-fpm.sock` with `pm = ondemand` and `pm.max_children = 24`. A drop-in at `$(brew --prefix)/etc/php/8.4/conf.d/99-worktree-kit.ini` sets `opcache.validate_timestamps = 1`, `opcache.revalidate_freq = 0`, and raises the opcache and realpath cache sizes, so edits show up immediately and one pool can hold many copies of a large app. Idle worktrees have no PHP workers. `putenv()` is cleared at the end of each request, so one worktree's `.env` does not leak into the next, and OPcache keys scripts by absolute path.

dnsmasq answers `*.test` (including one extra label) with `127.0.0.1`. The line in `$(brew --prefix)/etc/dnsmasq.conf` is `address=/.test/127.0.0.1`. The process is a system LaunchDaemon, `/Library/LaunchDaemons/sh.brew.dnsmasq.plist`, running as `nobody`:

```
dnsmasq --keep-in-foreground -C $(brew --prefix)/etc/dnsmasq.conf -7 $(brew --prefix)/etc/dnsmasq.d,*.conf
```

`/etc/resolver/test` contains `nameserver 127.0.0.1`. `brew services list` shows Caddy and dnsmasq as `none`; launchd is what is running them. PHP, MySQL, and Redis show as `started` under your user.

## Test databases

`{DB_DATABASE}_pest` for the source checkout, `{DB_DATABASE}_pest_{suffix}` for any other directory. `dir_match` is stripped from the directory name, hyphens become underscores, and the result is lowercased. MySQL identifiers are capped at 64 characters, and `artisan test --parallel` appends `_test_{n}`, so names are capped at 55. A longer name keeps the readable head and ends in `_{hash}` (six hex characters of the full suffix). Names within the limit are unchanged.

Archive drops a database only when the name in `TEST_DB_DATABASE` is exactly the name this worktree derives, that name differs from `DB_DATABASE`, and it matches `^[A-Za-z0-9_]+$` with length at most 64. `_` and `%` are escaped in the `LIKE` used to find parallel copies. Parallel copies `{name}_test_{n}` are dropped with it. A hyphenated name is refused. An inherited name that is not this worktree's derived name is cleared when `.env` is copied, and archive will not drop it.

Credentials come from `.env`: `DB_HOST` (default `127.0.0.1`), `DB_PORT` (`3306`), `DB_USERNAME` (`root`). `DB_PASSWORD` is passed through `MYSQL_PWD`. The database is `utf8mb4` / `utf8mb4_unicode_ci`.

## Redis

Worktrees share one Redis server. The prefix `{directory}:` cannot be a prefix of another worktree's name, because a directory name has no colon. Archive deletes keys under that prefix on every non-empty database (`SCAN`, then a literal prefix check, then `UNLINK` in batches of 500). It only sweeps when `REDIS_PREFIX` in `.env` is exactly `{directory}:`.

`redis-cli` is taken from `PATH`. If the app uses Redis and `redis-cli` is missing, archive prints an error and deletes nothing. `REDIS_HOST`, `REDIS_PORT`, and `REDIS_PASSWORD` come from `.env`. A password other than empty, `null`, or `false` is passed as `redis-cli -a`.

Horizon keys sit under `{directory}:horizon:`, which the `REDIS_PREFIX` sweep already covers.

## Dependency cloning

`vendor/` and `node_modules/` are copied from the source checkout with `cp -Rc` (APFS copy-on-write). The result is a real directory, so a later `composer require` stays in that worktree. Cloning is skipped, and a normal install runs instead, when the lockfile differs or is missing, when the source directory lacks a package its manifest declares, when the target already exists, or when the filesystem cannot clone. Composer packages named `php`, without a `/`, or starting with `ext-` are not required to be on disk. npm checks unscoped packages too.

`--fresh` moves the existing directory aside, clones again, and deletes the old copy after success. A failed install puts the old copy back.

## Paseo

Paseo creates the worktree and runs the two commands as hooks: `worktree-setup` on create, `worktree-archive` on remove.

`workspace-links.json` is the sidebar links file Paseo reads from the workspace root. By default it is one link, labeled `App`, pointing at `https://{domain}` or at `https://{link_subdomain}.{domain}` when `link_subdomain` is set. `links` replaces that list. `{domain}` in a URL is substituted. `links = []` writes nothing.

If the source checkout has `paseo.json` and the worktree's copy is not already a symlink, setup replaces it with a symlink. Paseo only reads that file.

## Claude Code settings

If the source checkout has `.claude/settings.local.json` and the worktree does not, setup copies it and prints `Copied .claude/settings.local.json from: {source}`. An existing file or symlink is left in place (`Keeping existing .claude/settings.local.json`). The copy is a regular file. Claude Code writes allow-answers into it, and a symlink would write those answers back into the source checkout.

## Cleanup

`worktree-archive` is the cleanup. It drops the derived test database and its `_test_{n}` copies, deletes Redis keys under the worktree prefix, and removes the Caddy site. It leaves the git worktree, `vendor/`, and `node_modules/` for Paseo to remove with the directory.

## Troubleshooting

**Could not detect this repository.** The worktree matched no `path_match`, `remote_match`, or `dir_match`. Nothing was changed. Exit code is 1.

**Could not create test database.** `TEST_DB_DATABASE` is empty. The Caddy site was not registered. Fix the MySQL error and run `worktree-setup` again.

**caddy reload failed, or no Caddyfile.** The site file is still written. Caddy is not running, or it was not started with `caddyfile`. The running process has to be the LaunchDaemon whose config is `~/.worktree-kit/Caddyfile`.

**no PHP-FPM socket.** `brew services` is not running `php@8.4`, or `php_fpm_socket` does not match the pool's `listen` line. The site file is still written.

**Archive deleted nothing in Redis.** `REDIS_PREFIX` is not `{directory}:`, the app is not configured for Redis, or `redis-cli` is not on `PATH`. A missing `redis-cli` is an error line, not a skip.

**Archive refused the test database.** The name in `.env` is not the derived name, it equals `DB_DATABASE`, or it is not a plain MySQL identifier. The source checkout's database is in the first two cases.

## Limitations

- macOS. Dependency cloning uses APFS `cp -c` and falls back to a real install elsewhere.
- One PHP version. Every worktree uses the shared `php@8.4` pool.
- Test databases are MySQL. Other engines need `skip_test_db`.
- `.env` values are the raw text after the first `=`. Quotes are not stripped.
- `REDIS_PASSWORD` is passed on the `redis-cli` command line.
- The Caddy wildcard matches one label.
- A MySQL name containing a hyphen is not created or dropped.
- `.claude/settings.local.json` is copied once. Later edits in the source checkout are not copied over an existing file.
