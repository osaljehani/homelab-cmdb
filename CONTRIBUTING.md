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

The project is built against real infrastructure, but the repo is public, so nothing in it may
carry real data. Every test under `tests/` is tracked, runs in CI, and uses synthetic fixtures only.

**If you add a new test:**

- Use only fictional data: hostnames like `testhost` or `*.example.lan`, RFC 5737 IPs
  (`192.0.2.x`, `198.51.100.x`, `203.0.113.x`), locally administered MACs (`02:00:5e:…`), and
  made-up usernames. Reuse the fixtures in `tests/conftest.py`; `cmdb/demo/data/` shows the
  pattern. Never copy real values from your own environment into a PR.
- If a test genuinely needs real data from your environment, put it under `tests/local/`. That
  directory is gitignored, so the test runs on your machine and never leaves it.
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
are thin shells over it. See the README's "Project layout" section for the full breakdown, and
[`docs/ROADMAP.md`](docs/ROADMAP.md) for planned features.

## Questions

Open an issue — even a rough one is fine.
