# HomeLabCMDB

Python-based homelab CMDB. Ansible-fed, SQLite-backed. CLI + FastAPI/HTMX web UI.

## Setup

```bash
uv sync --all-groups
uv run cmdb db upgrade
```

## Key commands

```bash
just test                              # run all tests
uv run cmdb import ansible ./out/      # import ansible --tree output
uv run cmdb hosts list                 # list hosts
uv run cmdb serve                      # start web UI on :8080
```

## Architecture

All business logic lives in `cmdb/domain/services/`. CLI (`cmdb/cli/`) and Web (`cmdb/web/`) are thin shells over the domain layer.

Upsert key: `machine_id` (from `ansible_machine_id`). Re-importing the same host updates it, never duplicates.

## Tests

```bash
uv run pytest tests/ -v
uv run pytest tests/ -k "import"       # filter
```

## Docker

```bash
docker compose up
```

DB persists in `./data/cmdb.db`.

## Environment variables

| Var | Default | Purpose |
|---|---|---|
| `CMDB_DB_PATH` | `./cmdb.db` | SQLite file path |
| `CMDB_HOST` | `0.0.0.0` | Bind address |
| `CMDB_PORT` | `8080` | Port |
