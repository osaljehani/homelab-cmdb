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
- **On-demand collection**   pull facts and Docker state live over SSH from the CMDB host (via Ansible), instead of running export scripts on each device. See below.
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

## On-demand collection (agentless)

Instead of running export scripts on each device and uploading files, the CMDB host can reach
out over SSH and collect facts and Docker state itself, then load them through the same import
pipeline. Collection drives the `ansible` binary, so it needs:

1. The optional `collect` dependency group (bundled in the Docker image):

   ```bash
   uv sync --all-groups          # or: uv sync --group collect
   ```

2. An Ansible inventory describing your hosts, plus SSH access from the CMDB host. Point the
   CMDB at it (SSH user/key can also live in the inventory as `ansible_user` /
   `ansible_ssh_private_key_file`):

   ```bash
   export CMDB_ANSIBLE_INVENTORY=./inventory.ini
   export CMDB_ANSIBLE_USER=ansible            # optional override
   export CMDB_SSH_PRIVATE_KEY=~/.ssh/id_ed25519 # optional override
   ```

Then collect from the CLI or the **Collect** page in the web UI (which also adds a "Collect now"
button on each host's detail page):

```bash
uv run cmdb collect facts            # gather Ansible facts for all hosts
uv run cmdb collect docker           # gather docker ps for all hosts
uv run cmdb collect all              # facts then docker
uv run cmdb collect facts --limit web01   # a single host or group
```

Unreachable or failed hosts are reported in the run's notes; Docker collection skips a host on
failure rather than wiping its existing container set. The `scripts/*-export.sh` file-based path
still works as a fallback for hosts you can't SSH to from the controller.

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
uv run cmdb collect facts [--inventory PATH] [--limit HOST]   # collect facts over SSH
uv run cmdb collect docker [--inventory PATH] [--limit HOST]  # collect containers over SSH
uv run cmdb collect all [--inventory PATH] [--limit HOST]     # facts then docker
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
| `CMDB_ANSIBLE_INVENTORY` | _(unset)_ | Ansible inventory path used by `cmdb collect` |
| `CMDB_ANSIBLE_USER` | _(unset)_ | SSH user override for collection (else from inventory) |
| `CMDB_SSH_PRIVATE_KEY` | _(unset)_ | SSH private key path override for collection |

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
