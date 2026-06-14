# HomeLabCMDB

A homelab inventory and CMDB tool. Feed it Ansible facts and browse your infrastructure through a web UI or the CLI.

- **Backend:** FastAPI + SQLite (via SQLAlchemy/Alembic)
- **Frontend:** HTMX + Jinja2 templates, graphite terminal-style dark theme
- **CLI:** Typer
- **Import sources:** Ansible `setup` module facts, Docker (`docker ps`), Kubernetes (`kubectl`)

Hosts are upserted on `ansible_machine_id`, so re-importing the same machine updates it without creating duplicates.

## Features

- **Hosts**   identity, OS, hardware, network, security (AppArmor/SELinux/FIPS) and virtualization, imported from Ansible facts. Tag and search hosts.
- **Docker inventory**   track containers per host (image, state, ports, compose project). Re-importing a host replaces its container set, so removed containers disappear.
- **Kubernetes**   model clusters, nodes (by role) and namespaces, imported from `kubectl`.
- **Change history**   every import snapshots a host's meaningful fields and records a per-host diff timeline (e.g. a kernel upgrade or IP change), skipping volatile values like uptime.
- **Generate**   Ansible inventory (YAML/INI) and SSH config from your inventory.

See [`docs/ROADMAP.md`](docs/ROADMAP.md) for planned features.

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

## Collecting Docker containers

Run the export helper on each Docker host (it reads `docker ps`) and import the JSON. The host
must already exist in the CMDB (imported via Ansible)   containers are matched to it by hostname/FQDN.

```bash
# On the Docker host (requires docker + jq):
./scripts/docker-export.sh > my-host-docker.json

# Then import it (web UI Import page, or CLI):
uv run cmdb import docker ./my-host-docker.json
```

The JSON format is `{"host": "<hostname>", "containers": [ ... ]}`; raw
`docker ps --format '{{json .}}'` fields are also accepted. Re-importing a host replaces its
entire container set.

---

## CLI reference

```bash
uv run cmdb hosts list            # list all hosts
uv run cmdb hosts show <hostname> # show host detail
uv run cmdb hosts history <hostname> # show a host's change history (field diffs)
uv run cmdb hosts tag <hostname> <tag>   # add a tag
uv run cmdb hosts untag <hostname> <tag> # remove a tag
uv run cmdb import ansible <path> # import hosts from Ansible facts (file or directory)
uv run cmdb import docker <path>  # import containers from docker-export.sh JSON
uv run cmdb import k8s <path>     # import K8s topology from k8s-export.sh JSON
uv run cmdb generate inventory --format yaml|ini  # generate Ansible inventory
uv run cmdb serve                 # start the web UI
uv run cmdb db upgrade            # run database migrations
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
    models.py   SQLAlchemy models
    services/   All business logic lives here (ansible, docker_import, k8s, history, generate)
  db/           Session + Alembic migrations
scripts/        Export helpers (docker-export.sh, k8s-export.sh)
tests/          pytest test suite
docs/           Roadmap and additional docs
data/           Database volume mount (Docker)
```
