# worktree-kit

Python 3.11+ command-line tool for Laravel git worktrees. Each worktree gets a local HTTPS domain (one Caddy instance, one shared PHP-FPM pool), its own MySQL test database, and its own Redis prefix. `vendor/` and `node_modules/` are cloned with APFS copy-on-write when the lockfiles and the source install allow it.

The standard library is the only dependency. Configuration is TOML, read with `tomllib`. Released under the MIT license (`LICENSE`).

## Layout

- `worktree_kit/config.py` — defaults, TOML merge, repo detection
- `worktree_kit/env.py` — `.env` reads and the worktree key updates
- `worktree_kit/caddy.py` — site files, reload, legacy `php -S` shutdown, domains
- `worktree_kit/deps.py` — copy-on-write clones and the install fallback
- `worktree_kit/db.py` — test database names, create, drop
- `worktree_kit/redis.py` — prefixes and key deletion
- `worktree_kit/setup.py` — `worktree-setup`, including the Claude settings copy
- `worktree_kit/archive.py` — `worktree-archive`
- `worktree_kit/serve.py` — `worktree-serve`
- `worktree_kit/purge_herd.py` — `worktree-purge-herd` (Herd leftovers)
- `worktree_kit/cli.py` — argparse entry points
- `bin/` — scripts that put this repo on `sys.path` and call `cli`
- `install.sh` — writes `~/.worktree-kit.toml` and `~/.worktree-kit/Caddyfile`, and prints the one-time macOS setup
- `tests/` — `unittest` for pure functions
- `worktree-kit.toml.example` — the annotated config; keep it aligned with `DEFAULTS`

## Checks

From the repo root:

```bash
python3 -m py_compile worktree_kit/*.py bin/*
python3 -m unittest discover -s tests
python3 bin/worktree-setup --help
```

Repeat `--help` for `worktree-archive`, `worktree-serve`, and `worktree-purge-herd`.

Tests pass explicit filesystem paths. They must not read or write a real `~/.worktree-kit.toml`, and they must not talk to Caddy, MySQL, or Redis.

## Changing behavior

General defaults live in `DEFAULTS` in `worktree_kit/config.py`. Repo keys (`source`, `path_match`, `remote_match`, `dir_match`, `env_keys`, `skip_test_db`, `domain`, `link_subdomain`, `links`) are read at the call site. When you add a key, update the code, `worktree-kit.toml.example`, the README, and a test.

Comments should say why a step exists. `paseo.json` is a symlink because Paseo only reads it. `.claude/settings.local.json` is a copy because Claude Code writes allow-answers into it. Keep those reasons accurate if the steps change.

`worktree-purge-herd` and the Herd `redis-cli` fallback exist for machines moving off Laravel Herd. Serving itself is Caddy.

## Examples

Use placeholder names (`myapp`, `~/code/myapp`, `myapp.test`). Do not add personal paths, hostnames, or credentials, and do not hard-code behavior that only one app needs. Put that in the TOML config, or leave it out.
