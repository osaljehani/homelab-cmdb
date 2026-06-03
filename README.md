# HomeLabCMDB

A homelab inventory and CMDB tool. Feed it Ansible facts and browse your infrastructure through a web UI or the CLI.

- **Backend:** FastAPI + SQLite (via SQLAlchemy/Alembic)
- **Frontend:** HTMX + Jinja2 templates
- **CLI:** Typer
- **Import source:** Ansible `setup` module facts

Hosts are upserted on `ansible_machine_id`, so re-importing the same machine updates it without creating duplicates.

---

## Quick start with Docker

```bash
docker compose up
```

The web UI is available at http://localhost:8080. The database persists in `./data/cmdb.db`.

---

## Local setup

Requires Python 3.12+ and [uv](https://docs.astral.sh/uv/).

```bash
uv sync --all-groups
uv run cmdb db upgrade
uv run cmdb serve        # web UI on :8080
```

---

## Collecting Ansible facts

### Single host (localhost)

```bash
ansible -m setup localhost > homelab.json
```

### Multiple hosts via inventory file

```bash
ansible -i inventory.ini all -m setup > homelab.json
```

Both commands write all host output to a single file. The importer handles the multi-host format automatically, splitting each `hostname | SUCCESS =>` block and upserting each host separately.

If you prefer one file per host, use `--tree` to write directly into a directory:

```bash
ansible -i inventory.ini all -m setup --tree out/
```

---

## Importing facts

### Web UI

Open http://localhost:8080/import and upload the JSON file. Single-host and multi-host files are both accepted.

### CLI

```bash
# Import a single file
uv run cmdb import ansible ./homelab.json

# Import a directory of per-host files (from --tree)
uv run cmdb import ansible ./out/
```

---

## CLI reference

```bash
uv run cmdb hosts list           # list all hosts
uv run cmdb hosts show <id>      # show host detail
uv run cmdb import ansible <path> # import from file or directory
uv run cmdb serve                # start the web UI
uv run cmdb db upgrade           # run database migrations
```

---

## Development

```bash
uv sync --all-groups
uv run pytest tests/ -v          # run all tests
uv run pytest tests/ -k "import" # run filtered tests
uv run ruff check cmdb/          # lint
```

---

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `CMDB_DB_PATH` | `./cmdb.db` | Path to the SQLite database file |
| `CMDB_HOST` | `0.0.0.0` | Bind address for the web server |
| `CMDB_PORT` | `8080` | Port for the web server |

---

## Project layout

```
cmdb/
  cli/          CLI entry points (thin shell over domain)
  web/          FastAPI routes and Jinja2 templates (thin shell over domain)
  domain/
    models/     SQLAlchemy models
    services/   All business logic lives here
tests/          pytest test suite
data/           Database volume mount (Docker)
```
