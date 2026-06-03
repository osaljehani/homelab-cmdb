# HomeLabCMDB — Design Spec
**Date:** 2026-06-03  
**Status:** Approved by user, awaiting implementation plan

---

## Context

The user wants a Python-based homelab CMDB to replace manual inventory tracking. Inspired by RackPeek (a .NET CLI+Web YAML-based tool in the same repo) but with a different approach: Python, SQLite, and data fed primarily by Ansible `setup` module output rather than manual entry or auto-discovery agents. No agents run on nodes — data comes in via import (CLI or web upload), CLI input, and UI input. SSH-based host interrogation is a future consideration, not v1.

---

## Stack

| Layer | Choice |
|---|---|
| Language | Python 3.12+ |
| CLI | Typer |
| Web framework | FastAPI |
| Templates | Jinja2 + HTMX |
| ORM | SQLAlchemy 2.x |
| Database | SQLite (`cmdb.db`) |
| Migrations | Alembic |
| Validation | Pydantic v2 |
| Packaging | `pyproject.toml` + `uv` |

---

## Project Structure

```
homelab-cmdb/
├── cmdb/
│   ├── cli/                  # Typer CLI entry points
│   │   ├── main.py           # root app, subcommand registration
│   │   ├── hosts.py          # cmdb hosts *
│   │   ├── import_.py        # cmdb import *
│   │   ├── k8s.py            # cmdb k8s *
│   │   ├── generate.py       # cmdb generate *
│   │   └── serve.py          # cmdb serve
│   ├── web/                  # FastAPI app
│   │   ├── app.py            # FastAPI instance, router registration
│   │   ├── routes/
│   │   │   ├── dashboard.py
│   │   │   ├── hosts.py
│   │   │   ├── import_.py
│   │   │   ├── k8s.py
│   │   │   ├── generate.py
│   │   │   └── settings.py
│   │   └── templates/        # Jinja2 templates (one per route + partials)
│   │       ├── base.html
│   │       ├── dashboard.html
│   │       ├── hosts/
│   │       ├── k8s/
│   │       ├── import/
│   │       └── generate/
│   ├── domain/               # shared business logic
│   │   ├── models.py         # SQLAlchemy ORM models
│   │   ├── schemas.py        # Pydantic schemas (import/export shapes)
│   │   └── services/
│   │       ├── ansible.py    # parse ansible JSON, upsert hosts
│   │       ├── hosts.py      # CRUD, tagging
│   │       ├── k8s.py        # cluster/node management
│   │       └── generate.py   # inventory + ssh config generation
│   └── db/
│       ├── session.py        # SQLAlchemy engine + session factory
│       └── migrations/       # Alembic env + versions
├── tests/
│   ├── test_import.py
│   ├── test_generate.py
│   ├── test_hosts.py
│   └── test_k8s.py
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── justfile
```

**Key principle:** `cmdb.cli` and `cmdb.web` are thin shells. All logic lives in `cmdb.domain.services`. Adding a feature means writing it once in services, then wiring CLI + web route.

---

## Data Model

```
Host
├── id (PK)
├── machine_id (unique — dedup key from ansible_machine_id)
├── hostname, fqdn
├── system_vendor, product_name, product_version, serial, form_factor
├── os_family, os_distribution, os_version, os_release, kernel, pkg_mgr
├── arch, cpu_model, cpu_cores, cpu_threads, memory_mb
├── primary_ipv4, primary_interface, primary_mac, gateway
├── virt_type, virt_role
├── apparmor_status, selinux_status, fips (bool)
├── bios_vendor, bios_version, bios_date
├── board_vendor, board_name, board_serial
├── service_mgr
├── uptime_seconds (snapshot at import time)
├── raw_facts (JSON — full ansible output, nothing discarded)
├── last_seen (timestamp)
├── created_at (timestamp)
└── tags → many-to-many → Tag

Tag
└── name (unique, lowercase, trimmed)

host_tags (association table)
├── host_id → Host
└── tag_id → Tag

K8sCluster
├── id, name (unique), description
└── nodes → K8sNode

K8sNode
├── id
├── host_id → Host (FK)
├── cluster_id → K8sCluster (FK)
└── role: enum (control-plane | worker | etcd)

ImportLog
├── id, imported_at
├── source: enum (cli | web)
├── filename
├── hosts_upserted, hosts_failed
└── notes
```

**Deduplication:** upsert on `machine_id`. Re-importing the same host updates fields and `last_seen`. New hosts are inserted. Each import run writes one `ImportLog`.

**Raw facts:** `ansible_devices`, `ansible_mounts`, `ansible_lvm`, `ansible_interfaces` (full detail), `ansible_dns`, `ansible_all_ipv4_addresses`, `ansible_all_ipv6_addresses` are stored in `raw_facts` JSON rather than flattened columns. Accessible on the host detail page; no data is discarded.

---

## Ansible Import

**Source format:** JSON files produced by:
```bash
ansible -m setup --tree out/ <host-or-group>
```
Each file is named after the host and contains the full `ansible_facts` dict.

**Mapping (ansible key → DB column):**
```
ansible_hostname        → hostname
ansible_fqdn            → fqdn
ansible_machine_id      → machine_id  (dedup key)
ansible_system_vendor   → system_vendor
ansible_product_name    → product_name
ansible_product_version → product_version
ansible_product_serial  → serial
ansible_form_factor     → form_factor
ansible_os_family       → os_family
ansible_distribution    → os_distribution
ansible_distribution_version → os_version
ansible_distribution_release → os_release
ansible_kernel          → kernel
ansible_pkg_mgr         → pkg_mgr
ansible_architecture    → arch
ansible_processor[0]    → cpu_model   (first entry in list)
ansible_processor_cores → cpu_cores
ansible_processor_vcpus → cpu_threads
ansible_memtotal_mb     → memory_mb
ansible_default_ipv4.address   → primary_ipv4
ansible_default_ipv4.interface → primary_interface
ansible_default_ipv4.macaddress → primary_mac
ansible_default_ipv4.gateway   → gateway
ansible_virtualization_type    → virt_type
ansible_virtualization_role    → virt_role
ansible_apparmor.status        → apparmor_status
ansible_selinux.status         → selinux_status  (or False if disabled)
ansible_fips                   → fips
ansible_bios_vendor            → bios_vendor
ansible_bios_version           → bios_version
ansible_bios_date              → bios_date
ansible_board_vendor           → board_vendor
ansible_board_name             → board_name
ansible_board_serial           → board_serial
ansible_service_mgr            → service_mgr
ansible_uptime_seconds         → uptime_seconds
```

---

## CLI Commands

```bash
# Import
cmdb import ansible ./out/            # directory: imports all files
cmdb import ansible ./out/blade-14    # single host file

# Hosts
cmdb hosts list                       # table view
cmdb hosts list --tag proxmox         # filter by tag
cmdb hosts list --os debian           # filter by OS family
cmdb hosts show <hostname>            # full detail
cmdb hosts tag <hostname> <tag>       # add tag
cmdb hosts untag <hostname> <tag>     # remove tag
cmdb hosts delete <hostname>          # remove host

# Kubernetes
cmdb k8s cluster add <name> [--description]
cmdb k8s cluster list
cmdb k8s cluster delete <name>
cmdb k8s node add <hostname> <cluster> --role control-plane|worker|etcd
cmdb k8s node list <cluster>
cmdb k8s node remove <hostname> <cluster>

# Generate
cmdb generate inventory               # ansible inventory YAML to stdout
cmdb generate inventory --format ini  # INI format
cmdb generate inventory --out hosts.yaml
cmdb generate inventory --tag proxmox # scope to tag
cmdb generate ssh-config              # SSH config blocks to stdout
cmdb generate ssh-config --out ~/.ssh/config.d/homelab
cmdb generate ssh-config --tag proxmox

# Database
cmdb db upgrade                       # run pending Alembic migrations

# Serve
cmdb serve                            # default: 0.0.0.0:8080
cmdb serve --host 0.0.0.0 --port 8080
```

---

## Web UI Pages

| Route | Purpose |
|---|---|
| `/` | Dashboard: host count, OS breakdown, last import time, k8s cluster count |
| `/hosts` | Searchable/filterable host table (HTMX search + tag filter, no reload) |
| `/hosts/{hostname}` | Host detail: all facts in sections, collapsible raw JSON, inline tag management |
| `/import` | File upload (single JSON or zip), import results, import history |
| `/k8s` | Cluster list → node table per cluster, add/remove nodes |
| `/generate` | Preview + download ansible inventory and SSH config; filter by tag |
| `/settings` | DB path, port, other config |

Nav: Dashboard | Hosts | Kubernetes | Import | Generate

HTMX used for: host search/filter, tag add/remove, import progress, dashboard refresh.

---

## Config & Deployment

**Environment variables:**
| Var | Default | Purpose |
|---|---|---|
| `CMDB_DB_PATH` | `./cmdb.db` | SQLite file path |
| `CMDB_HOST` | `0.0.0.0` | Bind address |
| `CMDB_PORT` | `8080` | Port |
| `CMDB_SECRET_KEY` | auto-generated | Session signing |

**Docker:**
```yaml
services:
  cmdb:
    image: homelab-cmdb
    ports: ["8080:8080"]
    volumes:
      - ./data:/data
    environment:
      - CMDB_DB_PATH=/data/cmdb.db
```

Dockerfile: single container, uvicorn, entrypoint runs `cmdb db upgrade` before serving.

**Direct install:**
```bash
uv tool install homelab-cmdb
cmdb serve
```

**No auth in v1** — homelab trusted network assumption. HTTP Basic Auth or reverse proxy (Caddy/nginx) is the recommended approach if needed.

---

## Not in v1

- SSH client for live host interrogation (deferred)
- Auto-discovery / agents on nodes (explicitly excluded)
- Multi-user auth (trusted network assumed)
- Storage/mount/LVM detail pages (data is in raw_facts, no dedicated UI)

---

## Verification Plan

1. `cmdb import ansible ./out/` on a directory of real ansible JSON files → hosts appear in DB
2. Re-import same files → host count unchanged, `last_seen` updated
3. `cmdb generate inventory` → valid ansible inventory YAML that `ansible-inventory --list` accepts
4. `cmdb generate ssh-config` → valid SSH config blocks
5. `cmdb serve` → dashboard loads, host list shows imported hosts, host detail shows all fields
6. Web import: upload a JSON file via browser → host upserted, ImportLog entry created
7. K8s: add cluster, assign two hosts as nodes, verify topology page shows them
8. Docker: `docker compose up`, volume-mounted DB persists across container restart
