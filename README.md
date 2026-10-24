# worktree-kit

Git worktrees make parallel Laravel work possible, and they leave the repetitive part to you: a second `.env`, a second `vendor/`, a test database that will not collide with the one on the other branch, and a hostname that actually resolves. worktree-kit does that setup, and the matching teardown, for each worktree.

Every worktree is served at `https://{directory}.{tld}` by one [Caddy](https://caddyserver.com) instance and one shared PHP-FPM pool. A worktree can also nest under a project domain (`feat-auth.myapp.test`) and grow one tenant label past that (`acme.feat-auth.myapp.test`). Bringing a worktree up or down writes or deletes a small site file and reloads Caddy. Caddy itself stays up, so the other worktrees keep their open connections.

The tool does not create the git worktree. You (or Paseo, Conductor, or a hook) create the directory, then run `worktree-setup` inside it.

## Requirements

- macOS. `worktree-serve` opens the site with `open`. Dependency cloning uses `cp -c` (APFS clonefile). The Herd cleanup reads Laravel Herd's macOS config directory.
- Python 3.11 or newer. macOS does not ship it. The package uses only the standard library.
- Git.
- [Caddy](https://caddyserver.com), listening on port 443, with the Caddyfile from `install.sh`.
- dnsmasq, or another resolver that can answer `*.test`. A hosts file cannot express the wildcard, and tenant subdomains need it.
- PHP with PHP-FPM. The default socket is Homebrew's Apple Silicon path, `/opt/homebrew/var/run/php-fpm.sock`. Intel Homebrew is `/usr/local/var/run/php-fpm.sock`. One pool is shared by every worktree, so every app on the machine uses that PHP.
- A Laravel app: `artisan`, `public/`, and a `.env` in the source checkout.
- Composer, when `composer.json` exists and `vendor/` cannot be cloned.
- Node.js and npm, when `package.json` exists. The asset build is `npm run build`. Lockfiles that participate in cloning are `composer.lock` and `package-lock.json`.
- MySQL, server and the `mysql` client, when you want a per-worktree test database. Postgres and SQLite are not created by this tool; set `skip_test_db = true` for those apps.
- Redis and `redis-cli`, only if the app uses Redis. Archive skips the key sweep when `redis-cli` is absent.

APFS is what makes `vendor/` and `node_modules/` cheap to duplicate. On a filesystem that cannot clone, setup falls through to a normal install. The rest of the tool still runs.

## Install

```bash
git clone https://github.com/MACscr/worktree-kit.git
cd worktree-kit
./install.sh
```

The installer is interactive. It:

1. Asks which repos you work on and where the canonical checkout of each one lives. The suggested source path is `~/code/{repo}`.
2. Writes `~/.worktree-kit.toml`. If that file already exists, it asks before replacing it.
3. Writes `~/.worktree-kit/Caddyfile` if you do not already have one. The file imports `~/.worktree-kit/sites/*.caddy` and does not change again. It also turns on Caddy's internal CA and gives that CA a one-year intermediate, so local certificates are not renewing on Caddy's short default lifetime.
4. Appends `bin/` to `PATH` in `~/.zshrc`.
5. Prints the one-time machine setup when `python3` is older than 3.11, Caddy is not running, the PHP-FPM socket is missing, or `/etc/resolver/test` is missing. The printed steps install Python, set the PHP-FPM pool's `listen` and `pm`, start dnsmasq as your user, and run Caddy with `caddy run --config ~/.worktree-kit/Caddyfile` (a LaunchDaemon, because `caddy reload` targets that file and Homebrew's service would not). sudo is for `/etc/resolver`, port 53, and port 443. Homebrew refuses `sudo brew`. The installer prints the steps. It does not run them, and it does not start MySQL or Redis.

`pip install .` exposes the same four commands through the Python environment. You still need the TOML file and the Caddyfile. `bin/worktree-*` is the path `install.sh` puts on `PATH`; those scripts add the repo root to `sys.path` and do not need a virtualenv.

Open a new shell after installing. Annotated config for everything the installer does not ask about is in `worktree-kit.toml.example`.

## Quick start

```bash
# ~/.worktree-kit.toml names this repo and its source checkout.
git worktree add ~/code/myapp-feat-auth feat-auth
cd ~/code/myapp-feat-auth
worktree-setup
```

With `dir_match = "myapp"` and `tld = "test"`, that directory is served at `https://myapp-feat-auth.test`. `worktree-setup` does not open a browser. `worktree-serve` opens one after Caddy accepts the reload.

When the branch is done:

```bash
worktree-archive
cd ~/code/myapp
git worktree remove ~/code/myapp-feat-auth
```

Run archive from inside the worktree, while `.env` is still there. Archive does not delete the directory.

## Commands

`worktree-setup`, `worktree-archive`, and `worktree-serve` run from the worktree root (the directory that contains `artisan` and `.env`). `worktree-purge-herd` does not: it only reads Herd's site registry, from any directory.

### `worktree-setup`

`worktree-setup [--fresh]`

If the current directory does not match a repo in the config, setup prints a warning, changes nothing, and exits with status 1 so a hook does not treat that as success. Otherwise, in order:

1. **`.env`.** Copies the source checkout's `.env` when the worktree does not have one. An existing file is kept; re-running setup only rewrites the keys below. That is deliberate: a re-run is how you pick up a new socket or domain, and replacing the whole file would drop whatever you were testing.
2. **Domain and prefix keys.** See [What setup writes to `.env`](#what-setup-writes-to-env).
3. **`paseo.json`.** When the source checkout has one and the worktree's copy is not already a symlink, setup replaces a regular file with a symlink to the source. Paseo only reads the file, so one symlink keeps later edits. No file in the source means this step does nothing.
4. **Claude Code settings.** When the source checkout has `.claude/settings.local.json` and the worktree does not, setup creates `.claude/` and copies the file. See [Paseo and Claude Code](#paseo-and-claude-code).
5. **PHP dependencies.** Clones `vendor/` from the source checkout when it is safe (see [Dependency cloning](#dependency-cloning)). Otherwise runs `composer install`.
6. **JavaScript dependencies.** Same choice for `node_modules/` and `package-lock.json`. On the install path, `package-lock.json` is copied aside and restored in a `finally`, including when `npm install` fails, so a failure cannot leave npm's rewrite of the lock. Composer and npm stay quiet when they succeed. Their output is printed when they exit non-zero.
7. **Frontend build.** Runs `npm run build` when `package.json` exists, unless `public/build/manifest.json` (or `public/build/.vite/manifest.json`) is already newer than `package.json`, `package-lock.json`, `vite.config.*`, and every file under `resources/`. A project whose build does not emit that manifest rebuilds on every setup. `--fresh` always rebuilds.
8. **A cloned native module that will not load.** npm sometimes leaves a platform-specific binary in place that macOS then refuses to open. If `npm run build` fails that way, setup deletes `node_modules`, runs `npm install` once, and builds again. That install restores `package-lock.json` the same way.
9. **Laravel's compiled caches.** Deletes `bootstrap/cache/*.php` when `artisan` exists. Cloning `vendor/` does not regenerate `packages.php`, so a branch that no longer has a package still boots its service provider. `config.php` and the route cache go stale the same way once `.env` points at this worktree's domain.
10. **Filament assets.** When `vendor/filament` exists, runs `php artisan filament:assets`. Filament's CSS and JS live under `public/` and are gitignored, so a fresh worktree would 404 them. A failure here is a warning; setup continues.
11. **Test database.** As soon as `.env` is in place, and before dependencies are installed, an inherited `TEST_DB_DATABASE` that is not the name derived for this worktree is cleared. The database is created later, and the name is written only after that succeeds. Skipped when `skip_test_db` is true, and skipped when there is no `.env`. If MySQL refuses the create, the key is left unset and setup stops. The Caddy site is not registered when this step fails. A value that already matches this worktree is left in place, so a re-run does not forget a database that exists.
12. **Caddy.** Writes the site file, stops a legacy `php -S` left behind by an older layout, reloads Caddy, and removes `WORKTREE_PORT` from `.env`.
13. **Paseo links.** Writes `workspace-links.json` unless the repo sets `links = []`.

`--fresh` moves an existing `vendor/` or `node_modules/` to `.vendor.wt-old` / `.node_modules.wt-old` before provisioning. The old tree is deleted only after the new one is in place, and put back if the clone or install fails. A successful refresh prints how much data the old copies held. This is how a worktree created before cloning, which owns a full private copy of each directory, gives that disk back.

### `worktree-archive`

Drops this worktree's resources. The directory, `.env`, `vendor/`, and `node_modules/` stay; remove the worktree with git afterward.

- Drops the test database and its `{name}_test_{n}` copies only when the name is exactly the one setup derives for this directory, it differs from `DB_DATABASE`, and it is letters, digits, and underscores. An inherited name (the source checkout's database, still in a copied `.env`) is refused. When `TEST_DB_DATABASE` is empty, that derived name is what gets dropped. `skip_test_db = true` skips this. The SQL `LIKE` escapes `_` and `%`; a regex then requires an exact match, so archiving `api` does not drop `api_v3`.
- Deletes Redis keys for this worktree. See [Redis](#redis).
- Removes the Caddy site file, reloads Caddy, and stops a legacy `php -S` if one is still recorded.

Archive still takes the site down when the directory matches no configured repo. The hostname then uses the directory name and the default tld. A database is dropped only when its name passes the same check, derived from that directory name.

### `worktree-serve`

`worktree-serve [--no-open]`

Registers the current directory with Caddy and reloads. It does not rewrite domain keys in `.env`. It does remove `WORKTREE_PORT`, and it stops a legacy per-worktree PHP server. Without `--no-open` it runs `open` on `https://{domain}` after Caddy accepts the reload. A failed reload leaves the browser closed.

Re-running this in a worktree that still has the old `php -S` layout is the migration: the process is stopped, the site file takes over, and the port key disappears.

### `worktree-purge-herd`

For a machine that used to serve sites with [Laravel Herd](https://herd.laravel.com). Herd stores, per site, a symlink, an nginx config, and four certificate files under `~/Library/Application Support/Herd/config/valet` (Herd kept that layout; this command does not read a standalone Valet install). After Caddy is serving the worktrees those files are unused.

```bash
worktree-purge-herd              # dry run
worktree-purge-herd --dead-only  # only symlinks whose target is already gone
worktree-purge-herd --apply      # delete the generated files
```

The dry run prints the count and the first ten sites. `--apply` deletes symlinks (never a real directory that happens to sit in `Sites/`), nginx configs, and `.crt`, `.key`, `.csr`, `.conf` certificate files. It does not uninstall Herd, and it does not touch Herd's PHP, MySQL, Redis, or any worktree. Trusted copies of the certificates stay in the login keychain; the command prints the `security delete-certificate` invocation.

The domain suffix is the configured `tld` (`test` unless you change it).

## Configuration

Config is merged in this order, later winning:

1. Built-in defaults (`worktree_kit/config.py`)
2. `~/.worktree-kit.toml`
3. `./.worktree-kit.toml` in the worktree
4. `WORKTREE_KIT_SITES_DIR` and `WORKTREE_KIT_TLD`

`worktree-kit.toml.example` is the full general section plus a `myapp` repo. Copy it to `~/.worktree-kit.toml`.

### `[general]`

| Key | Default | Meaning |
| --- | --- | --- |
| `caddy_sites_dir` | `~/.worktree-kit/sites` | One `{worktree}.caddy` file per worktree |
| `caddyfile` | `~/.worktree-kit/Caddyfile` | The file `caddy reload` is pointed at |
| `caddy_run_dir` | `~/.worktree-kit/run` | Pid and log files from the old per-worktree `php -S` |
| `php_fpm_socket` | `/opt/homebrew/var/run/php-fpm.sock` | Shared PHP-FPM socket. Intel Homebrew: `/usr/local/var/run/php-fpm.sock` |
| `tld` | `test` | Used when a repo sets no `domain` |

`caddy_sites_dir` is also overridden by `WORKTREE_KIT_SITES_DIR`. `tld` is also overridden by `WORKTREE_KIT_TLD`. Changing `tld` does not update dnsmasq or `/etc/resolver`; those have to match or the new names will not resolve.

### `[repos.<name>]`

`<name>` is a label. The match patterns decide which worktree uses the table. Patterns are unanchored regular expressions (`re.search`). `myapp` matches a directory named `myapp-feat-auth` and a remote URL that contains `myapp`. Anchor them (`myapp(\.git)?$`) when one name is a prefix of another.

Detection order:

1. `path_match` against the full worktree path, across every repo.
2. Each repo in file order: `remote_match` against `git remote get-url origin`, then `dir_match` against the directory name. The first hit wins, so an earlier repo's `dir_match` hides a later repo's remote.
3. A repo that sets neither pattern uses its label for both.

| Key | Default | Meaning |
| --- | --- | --- |
| `source` | none | Canonical checkout. `.env`, `vendor/`, `node_modules/`, `paseo.json`, and Claude settings are taken from here |
| `path_match` | none | Regex against the absolute worktree path. Checked first |
| `remote_match` | the repo label | Regex against the origin URL |
| `dir_match` | the repo label | Regex against the directory name. Also stripped out of the directory name when the test database suffix is built |
| `env_keys` | `SESSION_DOMAIN`, `APP_URL`, `APP_DOMAIN` | Which of those three keys to rewrite. Other names in the list are ignored |
| `skip_test_db` | `false` | Skip creating and dropping the MySQL test database |
| `domain` | none | Base domain. Worktrees are served at `{directory}.{domain}`. The directory whose name is the first label of `domain` is served at `domain` itself, so the source checkout is `myapp.test` and not `myapp.myapp.test` |
| `link_subdomain` | none | Redirect the bare domain to `{link_subdomain}.{domain}`. Also the host of the default Paseo link |
| `links` | one "App" link | Paseo header buttons. Each `url` may contain `{domain}`. `[]` writes no `workspace-links.json` |

```toml
[general]
caddy_sites_dir = "~/.worktree-kit/sites"
caddyfile = "~/.worktree-kit/Caddyfile"
caddy_run_dir = "~/.worktree-kit/run"
php_fpm_socket = "/opt/homebrew/var/run/php-fpm.sock"
tld = "test"

[repos.myapp]
source = "~/code/myapp"
remote_match = "myapp"
dir_match = "myapp"
env_keys = ["SESSION_DOMAIN", "APP_URL", "APP_DOMAIN"]
# skip_test_db = true
# domain = "myapp.test"
# link_subdomain = "www"
# links = [
#   { label = "App", url = "https://{domain}" },
# ]
```

### What setup writes to `.env`

Values are the raw text after the first `=`. Quotes are not removed.

| Key | When | Value |
| --- | --- | --- |
| `SESSION_DOMAIN` | the key already exists and is listed in `env_keys` | `.{domain}` (leading dot, so the cookie is sent to subdomains) |
| `APP_URL` | the key already exists and is listed in `env_keys` | `https://{domain}` |
| `APP_DOMAIN` | the key already exists and is listed in `env_keys` | `{domain}` |
| `SESSION_COOKIE` | `SESSION_DOMAIN` exists | `{directory}-session` |
| `CACHE_PREFIX` | `CACHE_STORE` is `redis` or `memcached` | `{directory}:cache:` |
| `REDIS_PREFIX` | the app uses Redis (below) | `{directory}:` |
| `HORIZON_PREFIX` | `config/horizon.php` exists | `{directory}:horizon:` |
| `TEST_DB_DATABASE` | the test database was created | the name from [Test databases](#test-databases) |
| `WORKTREE_PORT` | setup or serve | removed |

`{domain}` is `worktree_domain`: `{directory}.{tld}`, or `{directory}.{domain}` when the repo sets `domain`, or the bare `domain` for the source checkout's own directory.

`SESSION_COOKIE` exists because every worktree is a subdomain of one parent when `domain` is set, and Laravel's default cookie name is a slug of `APP_NAME`. A cookie set on the parent is sent to all of them. The worktree-specific name keeps logins apart. The same collision is why the Redis prefix exists: the default prefix is also a slug of `APP_NAME`, so sessions, queues, and cache would otherwise be one shared keyspace. Horizon replaces `REDIS_PREFIX` with `HORIZON_PREFIX`, which has the same default, so each worktree gets its own Horizon prefix too. `horizon:terminate` would otherwise stop every worktree's workers on the machine.

The app counts as using Redis when any of these is true:

- `REDIS_HOST` is present (a stock Laravel `.env` sets it, so setup writes `REDIS_PREFIX` even when the queue and cache are not Redis yet)
- `config/horizon.php` exists
- `QUEUE_CONNECTION`, `SESSION_DRIVER`, `CACHE_STORE`, or `CACHE_DRIVER` equals `redis`

MySQL credentials for create and drop come from the same `.env`: `DB_HOST` (default `127.0.0.1`), `DB_PORT` (`3306`), `DB_USERNAME` (`root`), `DB_PASSWORD` (empty). The client receives the value through `MYSQL_PWD`.

## Domains, Caddy, and TLS

The worktree's name is its directory name. A Codex worktree living at `.codex/worktrees/{id}/{repo}` is named `{id}-{repo}`, because the repo directory name alone would collide.

Caddy reads `~/.worktree-kit/Caddyfile`, which only imports the per-worktree files. `worktree-setup` and `worktree-serve` write `{caddy_sites_dir}/{directory}.caddy` and run `caddy reload`. Reload uses Caddy's admin API, needs no extra privileges, and does not restart the process. If Caddy is not running, the file is still written and the command says so.

A site with no `link_subdomain` looks like this. The socket path is whatever `php_fpm_socket` is; an absolute path shows up as `unix//...` because the directive is `unix/` plus the path:

```
*.feat-auth.myapp.test, feat-auth.myapp.test {
	tls internal
	root * "/path/to/worktree/public"
	php_fastcgi unix//opt/homebrew/var/run/php-fpm.sock
	file_server
}
```

`tls internal` asks Caddy's local CA for a certificate. The wildcard covers one label, and a wildcard does not cover the bare name, so both are listed. `acme.feat-auth.myapp.test` matches. `a.b.feat-auth.myapp.test` does not.

`php_fastcgi` does Laravel's front-controller routing (file, then directory index, then `index.php`) and sets `HTTPS` for the TLS listener, so generated URLs are https. `file_server` serves the static files.

`link_subdomain` changes the shape. An app that reads the tenant from the first label treats `feat-auth.myapp.test` as a request for a tenant named `feat-auth` and 404s. The bare domain redirects to `{link_subdomain}.{domain}`, and only the wildcard is served directly:

```
*.feat-auth.myapp.test {
	tls internal
	# ...
}

feat-auth.myapp.test {
	tls internal
	redir https://www.feat-auth.myapp.test{uri}
}
```

The pool should be `pm = ondemand`. An idle worktree then costs no worker. A page that fires several requests at once still gets a worker each. One pool serving many apps is safe because PHP clears `putenv()` at the end of each request, so one worktree's `.env` does not leak into the next request, and OPcache keys scripts by absolute path. Set `opcache.revalidate_freq=0` in `php.ini` or edits stay invisible until the cache decides to look. worktree-kit does not edit `php.ini`.

Trust the CA once with `caddy trust`. Caddy should run with `HOME` set to your user so that CA is created in your keychain. `brew services` does this. A launchd plist has to set `HOME` itself.

DNS is separate from the tool. The installer's suggestion is dnsmasq `address=/.test/127.0.0.1` and `/etc/resolver/test` containing `nameserver 127.0.0.1`.

Older checkouts of this tool ran `php -S` on a port stored as `WORKTREE_PORT`, with a pid file in `caddy_run_dir`. Setup, serve, and archive stop that process when the pid still belongs to `php -S`, then drop the env key on setup and serve. The pid is checked before it is signalled, because pids get reused.

## Test databases

Each worktree gets its own MySQL database. The base name is `DB_DATABASE`, or `laravel` when that key is missing.

`dir_match` is removed from the directory name (it is a regex, and every match is removed). Leading `-` and `_` are stripped, remaining hyphens become underscores, and the result is lowercased.

- No suffix left (`myapp` with `dir_match = "myapp"`): `{base}_pest`, for example `appdb_pest`.
- A suffix (`myapp-feat-auth`): `{base}_pest_{suffix}`, for example `appdb_pest_feat_auth`.

MySQL identifiers stop at 64 characters. `php artisan test --parallel` creates `{name}_test_{n}` alongside the database, so the name itself is capped at 55 characters (`_test_999` reserved). A longer name keeps the readable head and ends in `_{6 hex chars}` of a SHA-256 of the suffix, so two long branches that share a prefix do not land on one database. Names that already fit are not rewritten, which keeps databases created before the cap.

The database is created `utf8mb4` / `utf8mb4_unicode_ci` before `TEST_DB_DATABASE` is written. Tests pick it up from `.env`:

```bash
TEST_DB=$(grep "^TEST_DB_DATABASE=" .env | cut -d'=' -f2)
APP_ENV=testing DB_DATABASE=$TEST_DB php artisan test --parallel
```

Archive drops that database and the `_test_{n}` copies when the name is the one derived for this worktree and is not `DB_DATABASE`. A `{base}_pest` database is not treated as a prefix of every feature database.

## Redis

Worktrees share one Redis server. Setup namespaces the keys as above. Archive deletes them again.

Archive reads `REDIS_PREFIX` and deletes keys only when the value is exactly `{directory}:`, the prefix setup writes. A hand-edited or inherited prefix is left alone. The name ends in a colon because a directory name cannot contain one, so `api:` cannot be a prefix of `api-v2:`.

`CACHE_PREFIX` and `HORIZON_PREFIX` sit under `{directory}:`, so one sweep removes those too.

The sweep uses `SCAN` on every non-empty database (`INFO keyspace`), then checks the prefix literally, then `UNLINK`. Laravel's default split across databases 0, 1, and 2 is why every database is visited. `redis-cli` is taken from `PATH`, then from `~/Library/Application Support/Herd/bin`, because a Homebrew-less Herd install leaves `redis-cli` off `PATH` in non-interactive shells. `REDIS_HOST`, `REDIS_PORT`, and `REDIS_PASSWORD` come from `.env`. A password other than empty, `null`, or `false` is passed as `redis-cli -a`.

## Dependency cloning

Installing is the slow part of a new worktree, and most branches want exactly what the source checkout already has. On APFS, `cp -Rc` clones `vendor/` or `node_modules/` copy-on-write: the copy is a real directory, shares disk with the original until a file is written, and a later `composer require` in one worktree does not touch the others.

Cloning runs only when all of these hold:

- `source` is set and that directory exists in the source checkout
- the worktree does not already have the directory (or `--fresh` moved it aside)
- the lockfile is byte-for-byte the same (`composer.lock` or `package-lock.json`). A missing lock on either side refuses the clone
- the source tree actually contains the packages the manifest declares. A lockfile proves the source resolved the same versions, not that the install on disk is complete. An incomplete source would otherwise be copied into every new worktree
- `cp -c` works on that filesystem, and `cp -Rc` succeeds

The package check for Composer is `require` and `require-dev`, skipping `php` and `ext-*`. For npm it is `dependencies` and `devDependencies`, including unscoped names such as `vite`. Anything else (`vendor/` already present, lockfile drift, a missing package, a filesystem that cannot clone) runs `composer install` or `npm install` instead. A failed `cp` deletes the partial copy before that install, so a half-written tree is not mistaken for a finished one.

## Paseo and Claude Code

Both steps are no-ops unless `source` points at a checkout that has the file. They run only after a repo was detected.

**Paseo.** `paseo.json` in the worktree becomes a symlink to the source checkout's file, replacing a regular file if one was snapshotted into the worktree. An existing symlink is left alone. Setup also writes `workspace-links.json` at the worktree root: a JSON list of `{label, url}` buttons. The default is a single "App" button at `https://{domain}`, or `https://{link_subdomain}.{domain}` when `link_subdomain` is set. `links` in the repo config replaces that list. `links = []` writes nothing.

**Claude Code.** `.claude/settings.local.json` is where Claude Code stores "always allow" answers. Setup copies it. A symlink would write one worktree's approvals back into the source checkout, and from there into every other worktree. If the worktree already has the file (or a symlink), setup prints `Keeping existing .claude/settings.local.json` and leaves it. Delete that file and run setup again to copy a newer one. There is no ongoing sync.

Conductor, or any other tool that can run a script when a workspace is created or removed, can call `worktree-setup`, `worktree-archive`, and `worktree-serve` the same way.

## Cleanup

`worktree-archive` removes:

- the MySQL test database and its `_test_{n}` copies
- Redis keys under this worktree's generated prefix
- the Caddy site file
- a leftover `php -S` process, if the pid file still points at one

It leaves the worktree directory, git metadata, `.env`, `vendor/`, `node_modules/`, `workspace-links.json`, and the Claude settings file. `git worktree remove` is the step that deletes the directory. Run archive first.

`worktree-purge-herd` is the separate, one-time cleanup for Herd site files. It is not part of archive, and it does not have to be run from a worktree.

## Troubleshooting

**`Could not detect this repository`.** The directory matched no `path_match`, `remote_match`, or `dir_match`. Nothing was changed, and the command exits 1. Check `git remote get-url origin` and the directory name against `~/.worktree-kit.toml`. Patterns are unanchored regexes, and repo order matters.

**`no PHP-FPM socket`.** The site file was still written. Start the pool and point `listen` at `php_fpm_socket`. `pm = ondemand` is the setting that keeps idle worktrees cheap.

**`caddy reload failed` or `no Caddyfile`.** Caddy is not running, or `caddyfile` does not point at the file `install.sh` wrote. Start Caddy and run `worktree-serve`.

**The hostname does not resolve.** `/etc/resolver/test` and dnsmasq's `address=/.test/127.0.0.1` have to agree with `tld`. Nested names (`acme.feat-auth.myapp.test`) need the wildcard, not a single hosts entry.

**The certificate is not trusted.** Run `caddy trust`. If Caddy was started without your `HOME`, the CA may be in root's keychain instead of yours.

**`Could not create test database`.** The mysql client's error is printed above that message. Usual causes are a stopped server or credentials in `.env`. An inherited `TEST_DB_DATABASE` was already cleared when `.env` was copied, and a failed create leaves the key unset. Fix the cause and run `worktree-setup` again. The site is not registered until this succeeds.

**`vendor/` was installed instead of cloned.** The lockfile differs, the source tree is missing a declared package, the worktree already had a `vendor/`, or the disk cannot clone. `--fresh` replaces an existing directory. A branch that changed dependencies should install; that part is working.

**`npm run build` failed.** Setup raises and includes the tail of the output. If the failure was a cloned native module, it has already retried with a real `npm install` once.

**Sessions or queued jobs show up on the wrong branch.** Re-run `worktree-setup` so `SESSION_COOKIE`, `REDIS_PREFIX`, and `HORIZON_PREFIX` are written. A queue worker started before that still has the old prefix; restart it.

**Archive deleted nothing in Redis.** The prefix in `.env` is not `{directory}:` or `{directory}-`, Redis is not configured, or `redis-cli` is not on `PATH` or in Herd's bin directory.

**Herd sites are still registered.** `worktree-purge-herd`, then `--apply` when the dry run looks right.

## Limitations

- macOS is the supported host. There is no Linux or Windows path for the browser, the clone, or the Herd cleanup.
- Every worktree shares one PHP version and one FPM pool.
- Test databases are MySQL only. The `mysql` client has to be on `PATH`.
- npm is the only JavaScript installer, and `package-lock.json` is the only lockfile it will clone from. Composer uses `composer.lock`.
- The freshness check for an existing frontend build looks for a Vite manifest. Other build tools rebuild on every setup.
- `.env` values are not unquoted. `CACHE_STORE="redis"` does not match `redis`.
- Setup does not run migrations, seeders, or queue workers, and it does not create the git worktree.
- A directory name is also a hostname label and a Redis prefix. A colon in the name would break both the host and the "prefixes cannot nest" rule.
- The TLS wildcard is a single label.
- `REDIS_PASSWORD` is passed to `redis-cli` as an argument, so it is visible to other users on the machine for the duration of the command.
- Claude settings are copied once. Later edits in the source checkout are not pushed out. `paseo.json` is the opposite: it stays linked because nothing writes through it.
- `opcache.revalidate_freq` is your php.ini's job. The tool does not change it.

## Development

Python 3.11+, standard library only. Contributor notes, the module map, and the check commands are in `CLAUDE.md`.

```bash
python3 -m unittest discover -s tests
```

## License

MIT. Copyright 2026 Mark Chaney. See `LICENSE`.
