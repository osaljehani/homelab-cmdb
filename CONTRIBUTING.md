# Contributing

Thanks for considering a contribution to HomeLabCMDB. This is a homelab-scale project maintained
in spare time — small, focused PRs are easiest to review and merge.

## Dev setup

```bash
uv sync --all-groups
uv run cmdb db upgrade
```

Run the test suite and linter before opening a PR:

```bash
just test                  # or: uv run pytest -v
uv run ruff check .
```

## Test data policy

This is the one thing that's different from a typical repo, so read carefully.

The project runs against real homelab infrastructure, and some tests were written against real
hostnames, IPs, and usernames. Those can't be published. The split:

- **Tracked tests** — `tests/test_*.py` files that already use fictional data (`testhost`,
  `192.168.1.x`, `example.lan`) are committed normally and run in CI.
- **Maintainer-local tests** — some `tests/test_*.py` files contain private homelab data. They're
  gitignored (see the `tests/test_*.py` / `!tests/test_*-example.py` pattern) and never leave the
  maintainer's machine.
- **Scrubbed example copies** — for every gitignored real test, a `tests/*-example.py` copy is
  committed with the data replaced by fictional equivalents. CI materializes these into real
  `test_*.py` files on a fresh clone (copying `foo-example.py` → `foo.py` if `foo.py` doesn't
  already exist) so the suite is fully runnable without any private data.

**If you add a new test:**

- Use only fictional data — `testhost`, `192.168.1.x`, `example.lan`, made-up MAC addresses, etc.
  Never copy real values from your own environment into a PR.
- Commit it under its real `test_*.py` name (not as a `-example.py` file — that convention is only
  for scrubbed copies of maintainer-local tests). If `.gitignore` refuses to track it because it
  matches the `tests/test_*.py` pattern, force-add it:

  ```bash
  git add -f tests/test_your_new_thing.py
  ```

- Double-check `git diff --cached` before committing — no real hostnames, IPs, MACs, or usernames.

## Branches and commits

- Branch off `master`, one feature or fix per branch.
- Commit messages follow conventional-commit style: `feat(scope): ...`, `fix(scope): ...`,
  `docs: ...`, `chore: ...`, `ci: ...`. Check `git log --oneline -15` for the house style before
  your first commit.
- Open PRs against `master`. CI (ruff + pytest) must be green before merge.
- Update `CHANGELOG.md` under `[Unreleased]` for any user-visible change.

## Code layout

Business logic lives in `cmdb/domain/services/`; the CLI (`cmdb/cli/`) and web app (`cmdb/web/`)
are thin shells over it. See `CLAUDE.md` for more on architecture if you use Claude Code.

## Questions

Open an issue — even a rough one is fine.
