# HomeLabCMDB Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python-based homelab CMDB that ingests Ansible facts, stores them in SQLite, and exposes a CLI + FastAPI/HTMX web UI for inventory management, asset tagging, k8s topology tracking, and config generation.

**Architecture:** Thin CLI (Typer) and Web (FastAPI + Jinja2 + HTMX) shells over a shared domain layer (`cmdb.domain`). All business logic lives in `cmdb.domain.services`; both interfaces call those functions directly. SQLAlchemy 2.x + Alembic for persistence. Upsert-on-`machine_id` for idempotent Ansible re-imports.

**Tech Stack:** Python 3.12+, FastAPI, Uvicorn, Jinja2, HTMX, Typer, Rich, SQLAlchemy 2.x, Alembic, Pydantic v2, pydantic-settings, PyYAML, python-multipart, pytest, httpx

**Natural breakpoint:** Tasks 1–13 deliver a fully working CLI tool with no web dependency. Tasks 14–21 add the web UI and Docker deployment.

---

## File Map

```
HomeLabCMDB/
├── cmdb/
│   ├── __init__.py
│   ├── config.py                          # pydantic-settings env config
│   ├── cli/
│   │   ├── __init__.py
│   │   ├── main.py                        # Typer root app, subcommand wiring
│   │   ├── hosts.py                       # cmdb hosts *
│   │   ├── import_.py                     # cmdb import *
│   │   ├── k8s.py                         # cmdb k8s *
│   │   ├── generate.py                    # cmdb generate *
│   │   └── db.py                          # cmdb db upgrade
│   ├── web/
│   │   ├── __init__.py
│   │   ├── app.py                         # FastAPI instance, lifespan, router wiring
│   │   ├── deps.py                        # shared FastAPI dependencies (get_db, templates)
│   │   ├── routes/
│   │   │   ├── __init__.py
│   │   │   ├── dashboard.py
│   │   │   ├── hosts.py
│   │   │   ├── import_.py
│   │   │   ├── k8s.py
│   │   │   ├── generate.py
│   │   │   └── settings.py
│   │   └── templates/
│   │       ├── base.html
│   │       ├── dashboard.html
│   │       ├── hosts/
│   │       │   ├── list.html
│   │       │   ├── detail.html
│   │       │   └── _tag_list.html         # HTMX partial
│   │       ├── import/
│   │       │   └── index.html
│   │       ├── k8s/
│   │       │   └── index.html
│   │       └── generate/
│   │           └── index.html
│   ├── domain/
│   │   ├── __init__.py
│   │   ├── models.py                      # SQLAlchemy ORM models
│   │   ├── schemas.py                     # Pydantic schemas for API I/O
│   │   └── services/
│   │       ├── __init__.py
│   │       ├── ansible.py                 # parse + upsert ansible facts
│   │       ├── hosts.py                   # host CRUD + tagging
│   │       ├── k8s.py                     # cluster/node management
│   │       └── generate.py                # inventory + ssh config generation
│   └── db/
│       ├── __init__.py
│       ├── session.py                     # engine, SessionLocal, get_session(), get_db()
│       └── migrations/
│           ├── env.py
│           ├── script.py.mako
│           └── versions/
│               └── 001_initial.py
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   └── blade14.json                   # sample ansible facts for tests
│   ├── test_ansible_import.py
│   ├── test_hosts_service.py
│   ├── test_k8s_service.py
│   ├── test_generate_service.py
│   └── test_web.py
├── docs/
│   └── superpowers/
│       ├── specs/
│       └── plans/
├── Dockerfile
├── docker-compose.yml
├── alembic.ini
├── pyproject.toml
└── justfile
```

---

## Task 1: Project Scaffold

**Files:**
- Create: `pyproject.toml`
- Create: `justfile`
- Create: `cmdb/__init__.py` (and all `__init__.py` stubs)
- Create: `cmdb/config.py`

- [ ] **Step 1: Initialize git repo and create directory structure**

```bash
cd /home/omsaj/projects/inventory-project/HomeLabCMDB
git init
mkdir -p cmdb/{cli,web/{routes,templates/{hosts,import,k8s,generate}},domain/services,db/migrations/versions}
mkdir -p tests/fixtures
touch cmdb/__init__.py \
      cmdb/cli/__init__.py \
      cmdb/web/__init__.py \
      cmdb/web/routes/__init__.py \
      cmdb/domain/__init__.py \
      cmdb/domain/services/__init__.py \
      cmdb/db/__init__.py \
      tests/__init__.py
```

- [ ] **Step 2: Write `pyproject.toml`**

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "homelab-cmdb"
version = "0.1.0"
description = "Homelab CMDB — Ansible-fed, SQLite-backed inventory"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "sqlalchemy>=2.0",
    "alembic>=1.14",
    "pydantic>=2.0",
    "pydantic-settings>=2.0",
    "typer>=0.13",
    "jinja2>=3.1",
    "python-multipart>=0.0.12",
    "aiofiles>=24.1",
    "rich>=13.0",
    "pyyaml>=6.0",
]

[project.scripts]
cmdb = "cmdb.cli.main:app"

[dependency-groups]
dev = [
    "pytest>=8.0",
    "httpx>=0.27",
]

[tool.pytest.ini_options]
testpaths = ["tests"]

[tool.hatch.build.targets.wheel]
packages = ["cmdb"]
```

- [ ] **Step 3: Write `cmdb/config.py`**

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CMDB_")

    db_path: str = "./cmdb.db"
    host: str = "0.0.0.0"
    port: int = 8080
    secret_key: str = "change-me-in-production"

    @property
    def db_url(self) -> str:
        return f"sqlite:///{self.db_path}"


settings = Settings()
```

- [ ] **Step 4: Write `justfile`**

```makefile
default:
    just --list

install:
    uv sync --all-groups

test:
    uv run pytest -v

test-one filter:
    uv run pytest -v -k "{{filter}}"

serve:
    uv run cmdb serve

db-upgrade:
    uv run cmdb db upgrade

fmt:
    uv run ruff format cmdb tests

lint:
    uv run ruff check cmdb tests
```

- [ ] **Step 5: Install dependencies**

```bash
cd /home/omsaj/projects/inventory-project/HomeLabCMDB
uv sync --all-groups
```

Expected: uv creates `.venv`, installs all packages.

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml justfile cmdb/ tests/
git commit -m "feat: project scaffold"
```

---

## Task 2: SQLAlchemy Models

**Files:**
- Create: `cmdb/domain/models.py`
- Create: `tests/test_models_smoke.py`

- [ ] **Step 1: Write failing smoke test**

```python
# tests/test_models_smoke.py
from cmdb.domain.models import Host, Tag, K8sCluster, K8sNode, ImportLog, K8sNodeRole, ImportSource


def test_host_model_has_machine_id():
    h = Host(machine_id="abc", hostname="test")
    assert h.machine_id == "abc"


def test_k8s_node_role_enum():
    assert K8sNodeRole.CONTROL_PLANE == "control-plane"
    assert K8sNodeRole.WORKER == "worker"
    assert K8sNodeRole.ETCD == "etcd"


def test_import_source_enum():
    assert ImportSource.CLI == "cli"
    assert ImportSource.WEB == "web"
```

- [ ] **Step 2: Run test — expect ImportError**

```bash
uv run pytest tests/test_models_smoke.py -v
```

Expected: FAIL — `ImportError: cannot import name 'Host' from 'cmdb.domain.models'`

- [ ] **Step 3: Write `cmdb/domain/models.py`**

```python
from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer,
    String, Text, Table, UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.types import JSON, Enum as SAEnum


class Base(DeclarativeBase):
    pass


host_tags = Table(
    "host_tags",
    Base.metadata,
    Column("host_id", Integer, ForeignKey("hosts.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)


class Host(Base):
    __tablename__ = "hosts"

    id = Column(Integer, primary_key=True)
    machine_id = Column(String, unique=True, nullable=False, index=True)
    hostname = Column(String, nullable=False)
    fqdn = Column(String)
    system_vendor = Column(String)
    product_name = Column(String)
    product_version = Column(String)
    serial = Column(String)
    form_factor = Column(String)
    os_family = Column(String)
    os_distribution = Column(String)
    os_version = Column(String)
    os_release = Column(String)
    kernel = Column(String)
    pkg_mgr = Column(String)
    arch = Column(String)
    cpu_model = Column(String)
    cpu_cores = Column(Integer)
    cpu_threads = Column(Integer)
    memory_mb = Column(Integer)
    primary_ipv4 = Column(String)
    primary_interface = Column(String)
    primary_mac = Column(String)
    gateway = Column(String)
    virt_type = Column(String)
    virt_role = Column(String)
    apparmor_status = Column(String)
    selinux_status = Column(String)
    fips = Column(Boolean)
    bios_vendor = Column(String)
    bios_version = Column(String)
    bios_date = Column(String)
    board_vendor = Column(String)
    board_name = Column(String)
    board_serial = Column(String)
    service_mgr = Column(String)
    uptime_seconds = Column(Integer)
    raw_facts = Column(JSON)
    last_seen = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    tags = relationship("Tag", secondary=host_tags, back_populates="hosts")
    k8s_nodes = relationship("K8sNode", back_populates="host", cascade="all, delete-orphan")


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    hosts = relationship("Host", secondary=host_tags, back_populates="tags")


class K8sNodeRole(str, Enum):
    CONTROL_PLANE = "control-plane"
    WORKER = "worker"
    ETCD = "etcd"


class K8sCluster(Base):
    __tablename__ = "k8s_clusters"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)

    nodes = relationship("K8sNode", back_populates="cluster", cascade="all, delete-orphan")


class K8sNode(Base):
    __tablename__ = "k8s_nodes"
    __table_args__ = (UniqueConstraint("host_id", "cluster_id"),)

    id = Column(Integer, primary_key=True)
    host_id = Column(Integer, ForeignKey("hosts.id"), nullable=False)
    cluster_id = Column(Integer, ForeignKey("k8s_clusters.id"), nullable=False)
    role = Column(SAEnum(K8sNodeRole), nullable=False)

    host = relationship("Host", back_populates="k8s_nodes")
    cluster = relationship("K8sCluster", back_populates="nodes")


class ImportSource(str, Enum):
    CLI = "cli"
    WEB = "web"


class ImportLog(Base):
    __tablename__ = "import_logs"

    id = Column(Integer, primary_key=True)
    imported_at = Column(DateTime, default=datetime.utcnow)
    source = Column(SAEnum(ImportSource), nullable=False)
    filename = Column(String)
    hosts_upserted = Column(Integer, default=0)
    hosts_failed = Column(Integer, default=0)
    notes = Column(Text)
```

- [ ] **Step 4: Run test — expect PASS**

```bash
uv run pytest tests/test_models_smoke.py -v
```

Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add cmdb/domain/models.py tests/test_models_smoke.py
git commit -m "feat: SQLAlchemy models"
```

---

## Task 3: DB Session and Alembic

**Files:**
- Create: `cmdb/db/session.py`
- Create: `alembic.ini`
- Modify: `cmdb/db/migrations/env.py`
- Create: `cmdb/db/migrations/versions/001_initial.py`

- [ ] **Step 1: Write `cmdb/db/session.py`**

```python
from contextlib import contextmanager
from typing import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from cmdb.config import settings

engine = create_engine(
    settings.db_url,
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False)


@contextmanager
def get_session() -> Generator[Session, None, None]:
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency."""
    with get_session() as session:
        yield session
```

- [ ] **Step 2: Initialize Alembic**

```bash
cd /home/omsaj/projects/inventory-project/HomeLabCMDB
uv run alembic init cmdb/db/migrations
```

This creates `alembic.ini` at the project root and `cmdb/db/migrations/env.py`.

- [ ] **Step 3: Update `alembic.ini` — set sqlalchemy.url**

Open `alembic.ini`, find the line `sqlalchemy.url = driver://user:pass@localhost/dbname` and replace it with:

```ini
sqlalchemy.url = sqlite:///./cmdb.db
```

Also update the `script_location` line:
```ini
script_location = cmdb/db/migrations
```

- [ ] **Step 4: Replace `cmdb/db/migrations/env.py`**

```python
from logging.config import fileConfig
from sqlalchemy import engine_from_config, pool
from alembic import context

from cmdb.domain.models import Base
from cmdb.config import settings

config = context.config
config.set_main_option("sqlalchemy.url", settings.db_url)

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    url = config.get_main_option("sqlalchemy.url")
    context.configure(url=url, target_metadata=target_metadata, literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
```

- [ ] **Step 5: Generate initial migration**

```bash
cd /home/omsaj/projects/inventory-project/HomeLabCMDB
uv run alembic revision --autogenerate -m "initial"
```

Expected: Creates a file in `cmdb/db/migrations/versions/` with `upgrade()` that creates all tables.

- [ ] **Step 6: Apply migration and verify**

```bash
uv run alembic upgrade head
```

Expected: `cmdb.db` file created, tables `hosts`, `tags`, `host_tags`, `k8s_clusters`, `k8s_nodes`, `import_logs` exist.

```bash
uv run python -c "
from sqlalchemy import create_engine, inspect
e = create_engine('sqlite:///./cmdb.db')
print(inspect(e).get_table_names())
"
```

Expected output: `['host_tags', 'hosts', 'import_logs', 'k8s_clusters', 'k8s_nodes', 'tags']`

- [ ] **Step 7: Commit**

```bash
git add cmdb/db/session.py alembic.ini cmdb/db/migrations/
git commit -m "feat: Alembic setup and initial migration"
```

---

## Task 4: Test Fixtures and conftest

**Files:**
- Create: `tests/conftest.py`
- Create: `tests/fixtures/blade14.json`

- [ ] **Step 1: Write `tests/fixtures/blade14.json`**

```json
{
  "ansible_facts": {
    "ansible_hostname": "blade14",
    "ansible_fqdn": "blade14.homelab.local",
    "ansible_machine_id": "aabbccdd11223344aabbccdd11223344",
    "ansible_system_vendor": "Dell Inc.",
    "ansible_product_name": "PowerEdge R430",
    "ansible_product_version": "01",
    "ansible_product_serial": "SN00001",
    "ansible_form_factor": "Rack Mount Chassis",
    "ansible_os_family": "Debian",
    "ansible_distribution": "Ubuntu",
    "ansible_distribution_version": "22.04",
    "ansible_distribution_release": "jammy",
    "ansible_kernel": "5.15.0-91-generic",
    "ansible_pkg_mgr": "apt",
    "ansible_architecture": "x86_64",
    "ansible_processor": ["0", "GenuineIntel", "Intel(R) Xeon(R) CPU E5-2630 v4 @ 2.20GHz", "1", "GenuineIntel", "Intel(R) Xeon(R) CPU E5-2630 v4 @ 2.20GHz"],
    "ansible_processor_cores": 10,
    "ansible_processor_vcpus": 20,
    "ansible_memtotal_mb": 65536,
    "ansible_default_ipv4": {
      "address": "192.168.0.14",
      "interface": "eth0",
      "macaddress": "aa:bb:cc:dd:ee:14",
      "gateway": "192.168.0.1"
    },
    "ansible_virtualization_type": "kvm",
    "ansible_virtualization_role": "host",
    "ansible_apparmor": {"status": "enabled"},
    "ansible_selinux": {"status": "disabled"},
    "ansible_fips": false,
    "ansible_bios_vendor": "Dell Inc.",
    "ansible_bios_version": "2.9.3",
    "ansible_bios_date": "09/21/2017",
    "ansible_board_vendor": "Dell Inc.",
    "ansible_board_name": "0MV3HR",
    "ansible_board_serial": "BRDSERIAL1",
    "ansible_service_mgr": "systemd",
    "ansible_uptime_seconds": 86400,
    "ansible_devices": {"sda": {"size": "1.82 TB"}},
    "ansible_mounts": [{"device": "/dev/sda1", "mount": "/", "fstype": "ext4"}],
    "ansible_lvm": {"lvs": {}, "pvs": {}, "vgs": {}},
    "ansible_all_ipv4_addresses": ["192.168.0.14"],
    "ansible_all_ipv6_addresses": ["fe80::1"]
  },
  "changed": false
}
```

- [ ] **Step 2: Write `tests/conftest.py`**

```python
import json
import pytest
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from cmdb.domain.models import Base


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    Base.metadata.drop_all(engine)


@pytest.fixture
def blade14_facts() -> dict:
    path = Path(__file__).parent / "fixtures" / "blade14.json"
    return json.loads(path.read_text())


@pytest.fixture
def blade14_facts_alt() -> dict:
    """Same machine_id as blade14, different hostname — for upsert tests."""
    path = Path(__file__).parent / "fixtures" / "blade14.json"
    data = json.loads(path.read_text())
    data["ansible_facts"]["ansible_hostname"] = "blade14-renamed"
    return data
```

- [ ] **Step 3: Verify fixtures load**

```bash
uv run pytest tests/ -v --collect-only 2>&1 | head -20
```

Expected: conftest loads without errors.

- [ ] **Step 4: Commit**

```bash
git add tests/conftest.py tests/fixtures/
git commit -m "test: conftest and sample ansible fixture"
```

---

## Task 5: Ansible Import Service

**Files:**
- Create: `cmdb/domain/services/ansible.py`
- Create: `tests/test_ansible_import.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_ansible_import.py
import pytest
from cmdb.domain.services.ansible import import_host, import_from_path
from cmdb.domain.models import ImportSource


def test_import_maps_hostname(db, blade14_facts):
    host = import_host(db, blade14_facts)
    assert host.hostname == "blade14"


def test_import_maps_machine_id(db, blade14_facts):
    host = import_host(db, blade14_facts)
    assert host.machine_id == "aabbccdd11223344aabbccdd11223344"


def test_import_maps_cpu_model(db, blade14_facts):
    host = import_host(db, blade14_facts)
    assert "Xeon" in host.cpu_model


def test_import_maps_primary_ipv4(db, blade14_facts):
    host = import_host(db, blade14_facts)
    assert host.primary_ipv4 == "192.168.0.14"


def test_import_maps_apparmor(db, blade14_facts):
    host = import_host(db, blade14_facts)
    assert host.apparmor_status == "enabled"


def test_import_maps_selinux(db, blade14_facts):
    host = import_host(db, blade14_facts)
    assert host.selinux_status == "disabled"


def test_import_preserves_raw_facts(db, blade14_facts):
    host = import_host(db, blade14_facts)
    assert host.raw_facts is not None
    assert "ansible_devices" in host.raw_facts


def test_import_upserts_on_machine_id(db, blade14_facts, blade14_facts_alt):
    host1 = import_host(db, blade14_facts)
    host2 = import_host(db, blade14_facts_alt)
    assert host1.id == host2.id
    assert host2.hostname == "blade14-renamed"


def test_import_missing_machine_id_raises(db):
    with pytest.raises(ValueError, match="ansible_machine_id"):
        import_host(db, {"ansible_facts": {"ansible_hostname": "orphan"}})


def test_import_from_path_single_file(db, tmp_path, blade14_facts):
    import json
    f = tmp_path / "blade14"
    f.write_text(json.dumps(blade14_facts))
    log = import_from_path(db, str(tmp_path), ImportSource.CLI)
    assert log.hosts_upserted == 1
    assert log.hosts_failed == 0


def test_import_from_path_idempotent(db, tmp_path, blade14_facts):
    import json
    f = tmp_path / "blade14"
    f.write_text(json.dumps(blade14_facts))
    import_from_path(db, str(tmp_path), ImportSource.CLI)
    log2 = import_from_path(db, str(tmp_path), ImportSource.CLI)
    assert log2.hosts_upserted == 1
    from cmdb.domain.models import Host
    assert db.query(Host).count() == 1
```

- [ ] **Step 2: Run tests — expect failures**

```bash
uv run pytest tests/test_ansible_import.py -v
```

Expected: ImportError or all FAIL.

- [ ] **Step 3: Write `cmdb/domain/services/ansible.py`**

```python
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from cmdb.domain.models import Host, ImportLog, ImportSource

_DIRECT_MAP: dict[str, str] = {
    "ansible_hostname": "hostname",
    "ansible_fqdn": "fqdn",
    "ansible_machine_id": "machine_id",
    "ansible_system_vendor": "system_vendor",
    "ansible_product_name": "product_name",
    "ansible_product_version": "product_version",
    "ansible_product_serial": "serial",
    "ansible_form_factor": "form_factor",
    "ansible_os_family": "os_family",
    "ansible_distribution": "os_distribution",
    "ansible_distribution_version": "os_version",
    "ansible_distribution_release": "os_release",
    "ansible_kernel": "kernel",
    "ansible_pkg_mgr": "pkg_mgr",
    "ansible_architecture": "arch",
    "ansible_processor_cores": "cpu_cores",
    "ansible_processor_vcpus": "cpu_threads",
    "ansible_memtotal_mb": "memory_mb",
    "ansible_virtualization_type": "virt_type",
    "ansible_virtualization_role": "virt_role",
    "ansible_service_mgr": "service_mgr",
    "ansible_uptime_seconds": "uptime_seconds",
    "ansible_fips": "fips",
    "ansible_bios_vendor": "bios_vendor",
    "ansible_bios_version": "bios_version",
    "ansible_bios_date": "bios_date",
    "ansible_board_vendor": "board_vendor",
    "ansible_board_name": "board_name",
    "ansible_board_serial": "board_serial",
}


def _extract(facts: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}

    for src, dst in _DIRECT_MAP.items():
        if src in facts:
            result[dst] = facts[src]

    # cpu_model: processor list is ["0", "GenuineIntel", "model name", ...]
    proc = facts.get("ansible_processor", [])
    result["cpu_model"] = proc[2] if len(proc) >= 3 else (proc[0] if proc else None)

    ipv4 = facts.get("ansible_default_ipv4") or {}
    result["primary_ipv4"] = ipv4.get("address")
    result["primary_interface"] = ipv4.get("interface")
    result["primary_mac"] = ipv4.get("macaddress")
    result["gateway"] = ipv4.get("gateway")

    apparmor = facts.get("ansible_apparmor") or {}
    result["apparmor_status"] = apparmor.get("status") if isinstance(apparmor, dict) else None

    selinux = facts.get("ansible_selinux") or {}
    result["selinux_status"] = selinux.get("status") if isinstance(selinux, dict) else None

    return result


def import_host(session: Session, raw_data: dict[str, Any]) -> Host:
    facts = raw_data.get("ansible_facts", raw_data)
    fields = _extract(facts)
    machine_id = fields.get("machine_id")
    if not machine_id:
        raise ValueError("ansible_machine_id not found in facts")

    host = session.query(Host).filter_by(machine_id=machine_id).first()
    if host is None:
        host = Host(machine_id=machine_id, created_at=datetime.now(timezone.utc))
        session.add(host)

    for key, val in fields.items():
        setattr(host, key, val)
    host.raw_facts = facts
    host.last_seen = datetime.now(timezone.utc)
    session.flush()
    return host


def import_from_path(session: Session, path: str, source: ImportSource) -> ImportLog:
    target = Path(path)
    files = [target] if target.is_file() else [f for f in target.iterdir() if f.is_file()]

    upserted = 0
    failed = 0
    errors: list[str] = []

    for f in files:
        try:
            data = json.loads(f.read_text())
            import_host(session, data)
            upserted += 1
        except Exception as e:
            failed += 1
            errors.append(f"{f.name}: {e}")

    log = ImportLog(
        source=source,
        filename=str(path),
        hosts_upserted=upserted,
        hosts_failed=failed,
        notes="\n".join(errors) or None,
    )
    session.add(log)
    session.flush()
    return log
```

- [ ] **Step 4: Run tests — expect all PASS**

```bash
uv run pytest tests/test_ansible_import.py -v
```

Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add cmdb/domain/services/ansible.py tests/test_ansible_import.py
git commit -m "feat: ansible import service with upsert-on-machine_id"
```

---

## Task 6: Host CRUD Service

**Files:**
- Create: `cmdb/domain/services/hosts.py`
- Create: `tests/test_hosts_service.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_hosts_service.py
import pytest
from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.hosts import (
    list_hosts, get_host, add_tag, remove_tag, delete_host
)


@pytest.fixture
def populated_db(db, blade14_facts):
    import_host(db, blade14_facts)
    return db


def test_list_hosts_returns_all(populated_db):
    hosts = list_hosts(populated_db)
    assert len(hosts) == 1
    assert hosts[0].hostname == "blade14"


def test_list_hosts_filter_by_tag(populated_db):
    add_tag(populated_db, "blade14", "proxmox")
    result = list_hosts(populated_db, tag="proxmox")
    assert len(result) == 1
    result_none = list_hosts(populated_db, tag="missing")
    assert len(result_none) == 0


def test_list_hosts_filter_by_os(populated_db):
    result = list_hosts(populated_db, os_family="Debian")
    assert len(result) == 1
    result_none = list_hosts(populated_db, os_family="RedHat")
    assert len(result_none) == 0


def test_get_host_found(populated_db):
    host = get_host(populated_db, "blade14")
    assert host is not None
    assert host.primary_ipv4 == "192.168.0.14"


def test_get_host_not_found(populated_db):
    assert get_host(populated_db, "nonexistent") is None


def test_add_tag(populated_db):
    host = add_tag(populated_db, "blade14", "proxmox")
    assert any(t.name == "proxmox" for t in host.tags)


def test_add_tag_lowercases(populated_db):
    host = add_tag(populated_db, "blade14", "PROXMOX")
    assert any(t.name == "proxmox" for t in host.tags)


def test_add_tag_idempotent(populated_db):
    add_tag(populated_db, "blade14", "proxmox")
    add_tag(populated_db, "blade14", "proxmox")
    host = get_host(populated_db, "blade14")
    assert sum(1 for t in host.tags if t.name == "proxmox") == 1


def test_add_tag_unknown_host_raises(populated_db):
    with pytest.raises(ValueError, match="not found"):
        add_tag(populated_db, "ghost", "proxmox")


def test_remove_tag(populated_db):
    add_tag(populated_db, "blade14", "proxmox")
    host = remove_tag(populated_db, "blade14", "proxmox")
    assert not any(t.name == "proxmox" for t in host.tags)


def test_delete_host(populated_db):
    assert delete_host(populated_db, "blade14") is True
    assert get_host(populated_db, "blade14") is None


def test_delete_host_not_found(populated_db):
    assert delete_host(populated_db, "ghost") is False
```

- [ ] **Step 2: Run tests — expect failures**

```bash
uv run pytest tests/test_hosts_service.py -v
```

Expected: ImportError or all FAIL.

- [ ] **Step 3: Write `cmdb/domain/services/hosts.py`**

```python
from sqlalchemy.orm import Session

from cmdb.domain.models import Host, Tag


def list_hosts(session: Session, tag: str | None = None, os_family: str | None = None) -> list[Host]:
    q = session.query(Host)
    if tag:
        q = q.filter(Host.tags.any(Tag.name == tag.lower().strip()))
    if os_family:
        q = q.filter(Host.os_family.ilike(f"%{os_family}%"))
    return q.order_by(Host.hostname).all()


def get_host(session: Session, hostname: str) -> Host | None:
    return session.query(Host).filter_by(hostname=hostname).first()


def add_tag(session: Session, hostname: str, tag_name: str) -> Host:
    host = get_host(session, hostname)
    if not host:
        raise ValueError(f"Host '{hostname}' not found")
    name = tag_name.lower().strip()
    tag = session.query(Tag).filter_by(name=name).first()
    if not tag:
        tag = Tag(name=name)
        session.add(tag)
        session.flush()
    if tag not in host.tags:
        host.tags.append(tag)
    return host


def remove_tag(session: Session, hostname: str, tag_name: str) -> Host:
    host = get_host(session, hostname)
    if not host:
        raise ValueError(f"Host '{hostname}' not found")
    name = tag_name.lower().strip()
    host.tags = [t for t in host.tags if t.name != name]
    return host


def delete_host(session: Session, hostname: str) -> bool:
    host = get_host(session, hostname)
    if not host:
        return False
    session.delete(host)
    return True
```

- [ ] **Step 4: Run tests — expect all PASS**

```bash
uv run pytest tests/test_hosts_service.py -v
```

Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add cmdb/domain/services/hosts.py tests/test_hosts_service.py
git commit -m "feat: host CRUD and tagging service"
```

---

## Task 7: K8s Service

**Files:**
- Create: `cmdb/domain/services/k8s.py`
- Create: `tests/test_k8s_service.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_k8s_service.py
import pytest
from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.k8s import (
    add_cluster, list_clusters, delete_cluster,
    add_node, list_nodes, remove_node
)
from cmdb.domain.models import K8sNodeRole


@pytest.fixture
def populated_db(db, blade14_facts):
    import_host(db, blade14_facts)
    return db


def test_add_cluster(populated_db):
    cluster = add_cluster(populated_db, "homelab-k3s", description="k3s cluster")
    assert cluster.id is not None
    assert cluster.name == "homelab-k3s"


def test_add_cluster_duplicate_raises(populated_db):
    add_cluster(populated_db, "homelab-k3s")
    with pytest.raises(Exception):
        add_cluster(populated_db, "homelab-k3s")
        populated_db.flush()


def test_list_clusters(populated_db):
    add_cluster(populated_db, "cluster-a")
    add_cluster(populated_db, "cluster-b")
    clusters = list_clusters(populated_db)
    assert len(clusters) == 2
    assert clusters[0].name == "cluster-a"


def test_delete_cluster(populated_db):
    add_cluster(populated_db, "to-delete")
    assert delete_cluster(populated_db, "to-delete") is True
    assert len(list_clusters(populated_db)) == 0


def test_delete_cluster_not_found(populated_db):
    assert delete_cluster(populated_db, "ghost") is False


def test_add_node(populated_db):
    add_cluster(populated_db, "k3s")
    node = add_node(populated_db, "blade14", "k3s", K8sNodeRole.CONTROL_PLANE)
    assert node.id is not None
    assert node.role == K8sNodeRole.CONTROL_PLANE


def test_add_node_updates_role(populated_db):
    add_cluster(populated_db, "k3s")
    add_node(populated_db, "blade14", "k3s", K8sNodeRole.WORKER)
    node = add_node(populated_db, "blade14", "k3s", K8sNodeRole.CONTROL_PLANE)
    assert node.role == K8sNodeRole.CONTROL_PLANE


def test_add_node_unknown_host_raises(populated_db):
    add_cluster(populated_db, "k3s")
    with pytest.raises(ValueError, match="Host"):
        add_node(populated_db, "ghost", "k3s", K8sNodeRole.WORKER)


def test_add_node_unknown_cluster_raises(populated_db):
    with pytest.raises(ValueError, match="Cluster"):
        add_node(populated_db, "blade14", "ghost", K8sNodeRole.WORKER)


def test_list_nodes(populated_db):
    add_cluster(populated_db, "k3s")
    add_node(populated_db, "blade14", "k3s", K8sNodeRole.CONTROL_PLANE)
    nodes = list_nodes(populated_db, "k3s")
    assert len(nodes) == 1
    assert nodes[0].host.hostname == "blade14"


def test_remove_node(populated_db):
    add_cluster(populated_db, "k3s")
    add_node(populated_db, "blade14", "k3s", K8sNodeRole.WORKER)
    assert remove_node(populated_db, "blade14", "k3s") is True
    assert len(list_nodes(populated_db, "k3s")) == 0


def test_remove_node_not_found(populated_db):
    add_cluster(populated_db, "k3s")
    assert remove_node(populated_db, "blade14", "k3s") is False
```

- [ ] **Step 2: Run tests — expect failures**

```bash
uv run pytest tests/test_k8s_service.py -v
```

- [ ] **Step 3: Write `cmdb/domain/services/k8s.py`**

```python
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from cmdb.domain.models import Host, K8sCluster, K8sNode, K8sNodeRole


def add_cluster(session: Session, name: str, description: str | None = None) -> K8sCluster:
    cluster = K8sCluster(name=name, description=description)
    session.add(cluster)
    session.flush()
    return cluster


def list_clusters(session: Session) -> list[K8sCluster]:
    return session.query(K8sCluster).order_by(K8sCluster.name).all()


def delete_cluster(session: Session, name: str) -> bool:
    cluster = session.query(K8sCluster).filter_by(name=name).first()
    if not cluster:
        return False
    session.delete(cluster)
    return True


def add_node(session: Session, hostname: str, cluster_name: str, role: K8sNodeRole) -> K8sNode:
    host = session.query(Host).filter_by(hostname=hostname).first()
    if not host:
        raise ValueError(f"Host '{hostname}' not found")
    cluster = session.query(K8sCluster).filter_by(name=cluster_name).first()
    if not cluster:
        raise ValueError(f"Cluster '{cluster_name}' not found")
    existing = session.query(K8sNode).filter_by(host_id=host.id, cluster_id=cluster.id).first()
    if existing:
        existing.role = role
        return existing
    node = K8sNode(host_id=host.id, cluster_id=cluster.id, role=role)
    session.add(node)
    session.flush()
    return node


def list_nodes(session: Session, cluster_name: str) -> list[K8sNode]:
    cluster = session.query(K8sCluster).filter_by(name=cluster_name).first()
    if not cluster:
        raise ValueError(f"Cluster '{cluster_name}' not found")
    return list(cluster.nodes)


def remove_node(session: Session, hostname: str, cluster_name: str) -> bool:
    host = session.query(Host).filter_by(hostname=hostname).first()
    cluster = session.query(K8sCluster).filter_by(name=cluster_name).first()
    if not host or not cluster:
        return False
    node = session.query(K8sNode).filter_by(host_id=host.id, cluster_id=cluster.id).first()
    if not node:
        return False
    session.delete(node)
    return True
```

- [ ] **Step 4: Run tests — expect all PASS**

```bash
uv run pytest tests/test_k8s_service.py -v
```

Expected: 13 passed.

- [ ] **Step 5: Commit**

```bash
git add cmdb/domain/services/k8s.py tests/test_k8s_service.py
git commit -m "feat: k8s cluster/node topology service"
```

---

## Task 8: Generate Service

**Files:**
- Create: `cmdb/domain/services/generate.py`
- Create: `tests/test_generate_service.py`

- [ ] **Step 1: Write failing tests**

```python
# tests/test_generate_service.py
import yaml
import pytest
from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.hosts import add_tag
from cmdb.domain.services.generate import (
    generate_inventory_yaml, generate_inventory_ini, generate_ssh_config
)


@pytest.fixture
def populated_db(db, blade14_facts):
    import_host(db, blade14_facts)
    return db


def test_inventory_yaml_contains_hostname(populated_db):
    out = generate_inventory_yaml(populated_db)
    data = yaml.safe_load(out)
    assert "blade14" in data["all"]["hosts"]


def test_inventory_yaml_contains_ansible_host(populated_db):
    out = generate_inventory_yaml(populated_db)
    data = yaml.safe_load(out)
    assert data["all"]["hosts"]["blade14"]["ansible_host"] == "192.168.0.14"


def test_inventory_yaml_tag_filter(populated_db):
    add_tag(populated_db, "blade14", "proxmox")
    out_all = generate_inventory_yaml(populated_db)
    out_tagged = generate_inventory_yaml(populated_db, tag="proxmox")
    out_none = generate_inventory_yaml(populated_db, tag="missing")
    assert "blade14" in yaml.safe_load(out_all)["all"]["hosts"]
    assert "blade14" in yaml.safe_load(out_tagged)["all"]["hosts"]
    assert not yaml.safe_load(out_none)["all"]["hosts"]


def test_inventory_ini_contains_hostname(populated_db):
    out = generate_inventory_ini(populated_db)
    assert "blade14" in out
    assert "ansible_host=192.168.0.14" in out


def test_inventory_ini_starts_with_all(populated_db):
    out = generate_inventory_ini(populated_db)
    assert out.startswith("[all]")


def test_ssh_config_contains_host_block(populated_db):
    out = generate_ssh_config(populated_db)
    assert "Host blade14" in out


def test_ssh_config_contains_hostname_line(populated_db):
    out = generate_ssh_config(populated_db)
    assert "HostName 192.168.0.14" in out or "HostName blade14.homelab.local" in out


def test_ssh_config_tag_filter(populated_db):
    add_tag(populated_db, "blade14", "proxmox")
    out_tagged = generate_ssh_config(populated_db, tag="proxmox")
    out_none = generate_ssh_config(populated_db, tag="missing")
    assert "Host blade14" in out_tagged
    assert out_none.strip() == ""
```

- [ ] **Step 2: Run tests — expect failures**

```bash
uv run pytest tests/test_generate_service.py -v
```

- [ ] **Step 3: Write `cmdb/domain/services/generate.py`**

```python
import yaml
from sqlalchemy.orm import Session

from cmdb.domain.models import Host, Tag


def _get_hosts(session: Session, tag: str | None = None) -> list[Host]:
    q = session.query(Host)
    if tag:
        q = q.filter(Host.tags.any(Tag.name == tag.lower().strip()))
    return q.order_by(Host.hostname).all()


def generate_inventory_yaml(session: Session, tag: str | None = None) -> str:
    hosts = _get_hosts(session, tag)
    inventory: dict = {"all": {"hosts": {}}}
    for host in hosts:
        entry: dict | None = {}
        if host.primary_ipv4:
            entry["ansible_host"] = host.primary_ipv4
        inventory["all"]["hosts"][host.hostname] = entry or None
    return yaml.dump(inventory, default_flow_style=False, allow_unicode=True)


def generate_inventory_ini(session: Session, tag: str | None = None) -> str:
    hosts = _get_hosts(session, tag)
    lines = ["[all]"]
    for host in hosts:
        if host.primary_ipv4:
            lines.append(f"{host.hostname} ansible_host={host.primary_ipv4}")
        else:
            lines.append(host.hostname)
    return "\n".join(lines) + "\n"


def generate_ssh_config(session: Session, tag: str | None = None) -> str:
    hosts = _get_hosts(session, tag)
    blocks: list[str] = []
    for host in hosts:
        lines = [f"Host {host.hostname}"]
        if host.fqdn and host.fqdn != host.hostname:
            lines.append(f"  HostName {host.fqdn}")
        elif host.primary_ipv4:
            lines.append(f"  HostName {host.primary_ipv4}")
        if host.primary_mac:
            lines.append(f"  # MAC: {host.primary_mac}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks) + "\n" if blocks else ""
```

- [ ] **Step 4: Run all tests — expect all PASS**

```bash
uv run pytest tests/ -v
```

Expected: all passing (models + ansible + hosts + k8s + generate).

- [ ] **Step 5: Commit**

```bash
git add cmdb/domain/services/generate.py tests/test_generate_service.py
git commit -m "feat: inventory and ssh config generation service"
```

---

## Task 9: CLI Scaffold

**Files:**
- Create: `cmdb/cli/main.py`
- Create: `cmdb/cli/db.py`

- [ ] **Step 1: Write `cmdb/cli/db.py`**

```python
import typer
from pathlib import Path

app = typer.Typer(help="Database management")


@app.command("upgrade")
def upgrade() -> None:
    """Run pending Alembic migrations."""
    from alembic.config import Config
    from alembic import command

    cfg = Config(str(Path(__file__).parent.parent.parent / "alembic.ini"))
    command.upgrade(cfg, "head")
    typer.echo("Migrations applied.")
```

- [ ] **Step 2: Write `cmdb/cli/main.py`**

```python
import typer

from cmdb.cli import hosts, import_, k8s, generate, db as db_cli

app = typer.Typer(name="cmdb", help="Homelab CMDB", no_args_is_help=True)
app.add_typer(hosts.app, name="hosts")
app.add_typer(import_.app, name="import")
app.add_typer(k8s.app, name="k8s")
app.add_typer(generate.app, name="generate")
app.add_typer(db_cli.app, name="db")


@app.command("serve")
def serve(
    host: str = typer.Option("0.0.0.0", help="Bind host"),
    port: int = typer.Option(8080, help="Port"),
) -> None:
    """Start the web UI."""
    import uvicorn
    from cmdb.web.app import app as fastapi_app

    uvicorn.run(fastapi_app, host=host, port=port)


if __name__ == "__main__":
    app()
```

- [ ] **Step 3: Verify CLI entry point resolves**

```bash
uv run cmdb --help
```

Expected: Shows `cmdb` help with subcommands listed (even if stubs haven't been written yet, the ones that exist should show).

- [ ] **Step 4: Commit**

```bash
git add cmdb/cli/main.py cmdb/cli/db.py cmdb/cli/__init__.py
git commit -m "feat: CLI scaffold and db upgrade command"
```

---

## Task 10: CLI Import and Hosts Commands

**Files:**
- Create: `cmdb/cli/import_.py`
- Create: `cmdb/cli/hosts.py`

- [ ] **Step 1: Write `cmdb/cli/import_.py`**

```python
import typer
from rich.console import Console

from cmdb.db.session import get_session
from cmdb.domain.models import ImportSource
from cmdb.domain.services.ansible import import_from_path

app = typer.Typer(help="Import data into CMDB")
console = Console()


@app.command("ansible")
def import_ansible(
    path: str = typer.Argument(..., help="Path to ansible --tree output dir or single file"),
) -> None:
    """Import hosts from ansible setup module JSON output."""
    with get_session() as session:
        log = import_from_path(session, path, ImportSource.CLI)
    console.print(f"[green]Imported:[/green] {log.hosts_upserted} upserted, {log.hosts_failed} failed")
    if log.notes:
        console.print(f"[yellow]Errors:[/yellow]\n{log.notes}")
```

- [ ] **Step 2: Write `cmdb/cli/hosts.py`**

```python
import typer
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from cmdb.db.session import get_session
from cmdb.domain.services.hosts import add_tag, delete_host, get_host, list_hosts, remove_tag

app = typer.Typer(help="Manage hosts", no_args_is_help=True)
console = Console()


@app.command("list")
def list_cmd(
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
    os: str | None = typer.Option(None, "--os", help="Filter by OS family"),
) -> None:
    """List all hosts."""
    with get_session() as session:
        hosts = list_hosts(session, tag=tag, os_family=os)

    table = Table(title=f"Hosts ({len(hosts)})")
    table.add_column("Hostname", style="cyan")
    table.add_column("IP")
    table.add_column("OS")
    table.add_column("CPU")
    table.add_column("RAM (MB)")
    table.add_column("Tags")

    for h in hosts:
        table.add_row(
            h.hostname,
            h.primary_ipv4 or "",
            f"{h.os_distribution or ''} {h.os_version or ''}".strip(),
            f"{h.cpu_cores or ''}c/{h.cpu_threads or ''}t",
            str(h.memory_mb or ""),
            ", ".join(t.name for t in h.tags),
        )
    console.print(table)


@app.command("show")
def show_cmd(hostname: str) -> None:
    """Show full details for a host."""
    with get_session() as session:
        host = get_host(session, hostname)
    if not host:
        console.print(f"[red]Host '{hostname}' not found[/red]")
        raise typer.Exit(1)

    content = (
        f"[bold]Hostname:[/bold]     {host.hostname}\n"
        f"[bold]FQDN:[/bold]         {host.fqdn or '-'}\n"
        f"[bold]Machine ID:[/bold]   {host.machine_id}\n"
        f"[bold]IP:[/bold]           {host.primary_ipv4 or '-'}\n"
        f"[bold]Gateway:[/bold]      {host.gateway or '-'}\n"
        f"[bold]MAC:[/bold]          {host.primary_mac or '-'}\n"
        f"[bold]OS:[/bold]           {host.os_distribution} {host.os_version} ({host.os_family})\n"
        f"[bold]Kernel:[/bold]       {host.kernel or '-'}\n"
        f"[bold]CPU:[/bold]          {host.cpu_model or '-'} ({host.cpu_cores}c/{host.cpu_threads}t)\n"
        f"[bold]RAM:[/bold]          {host.memory_mb} MB\n"
        f"[bold]Vendor:[/bold]       {host.system_vendor} {host.product_name}\n"
        f"[bold]Virt:[/bold]         {host.virt_type}/{host.virt_role}\n"
        f"[bold]AppArmor:[/bold]     {host.apparmor_status or '-'}\n"
        f"[bold]SELinux:[/bold]      {host.selinux_status or '-'}\n"
        f"[bold]Tags:[/bold]         {', '.join(t.name for t in host.tags) or 'none'}\n"
        f"[bold]Last seen:[/bold]    {host.last_seen}"
    )
    console.print(Panel(content, title=hostname))


@app.command("tag")
def tag_cmd(hostname: str, tag: str) -> None:
    """Add a tag to a host."""
    with get_session() as session:
        add_tag(session, hostname, tag)
    console.print(f"[green]Tagged '{hostname}' with '{tag}'[/green]")


@app.command("untag")
def untag_cmd(hostname: str, tag: str) -> None:
    """Remove a tag from a host."""
    with get_session() as session:
        remove_tag(session, hostname, tag)
    console.print(f"[green]Removed tag '{tag}' from '{hostname}'[/green]")


@app.command("delete")
def delete_cmd(
    hostname: str,
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation"),
) -> None:
    """Delete a host from the CMDB."""
    if not yes:
        typer.confirm(f"Delete host '{hostname}'?", abort=True)
    with get_session() as session:
        deleted = delete_host(session, hostname)
    if deleted:
        console.print(f"[green]Deleted '{hostname}'[/green]")
    else:
        console.print(f"[red]Host '{hostname}' not found[/red]")
        raise typer.Exit(1)
```

- [ ] **Step 3: Smoke-test CLI import and hosts**

```bash
# Apply migrations first
uv run cmdb db upgrade

# Import the fixture file
uv run cmdb import ansible tests/fixtures/blade14.json

# List hosts
uv run cmdb hosts list

# Show host detail
uv run cmdb hosts show blade14
```

Expected: `hosts list` shows blade14 with IP, OS, CPU/RAM. `hosts show` prints a panel with all fields.

- [ ] **Step 4: Commit**

```bash
git add cmdb/cli/import_.py cmdb/cli/hosts.py
git commit -m "feat: CLI import and hosts commands"
```

---

## Task 11: CLI K8s and Generate Commands

**Files:**
- Create: `cmdb/cli/k8s.py`
- Create: `cmdb/cli/generate.py`

- [ ] **Step 1: Write `cmdb/cli/k8s.py`**

```python
import typer
from rich.console import Console
from rich.table import Table

from cmdb.db.session import get_session
from cmdb.domain.models import K8sNodeRole
from cmdb.domain.services.k8s import (
    add_cluster, delete_cluster, list_clusters,
    add_node, list_nodes, remove_node
)

app = typer.Typer(help="Manage Kubernetes topology", no_args_is_help=True)
cluster_app = typer.Typer(help="Manage clusters", no_args_is_help=True)
node_app = typer.Typer(help="Manage cluster nodes", no_args_is_help=True)
app.add_typer(cluster_app, name="cluster")
app.add_typer(node_app, name="node")
console = Console()


@cluster_app.command("add")
def cluster_add(
    name: str,
    description: str = typer.Option("", "--description", "-d"),
) -> None:
    with get_session() as session:
        add_cluster(session, name, description or None)
    console.print(f"[green]Created cluster '{name}'[/green]")


@cluster_app.command("list")
def cluster_list() -> None:
    with get_session() as session:
        clusters = list_clusters(session)
        rows = [(c.name, c.description or "", str(len(c.nodes))) for c in clusters]

    table = Table(title="K8s Clusters")
    table.add_column("Name", style="cyan")
    table.add_column("Description")
    table.add_column("Nodes")
    for row in rows:
        table.add_row(*row)
    console.print(table)


@cluster_app.command("delete")
def cluster_delete(name: str, yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    if not yes:
        typer.confirm(f"Delete cluster '{name}' and all its node associations?", abort=True)
    with get_session() as session:
        ok = delete_cluster(session, name)
    if ok:
        console.print(f"[green]Deleted cluster '{name}'[/green]")
    else:
        console.print(f"[red]Cluster '{name}' not found[/red]")
        raise typer.Exit(1)


@node_app.command("add")
def node_add(
    hostname: str,
    cluster: str,
    role: K8sNodeRole = typer.Option(..., "--role", help="control-plane, worker, or etcd"),
) -> None:
    with get_session() as session:
        add_node(session, hostname, cluster, role)
    console.print(f"[green]Added '{hostname}' to '{cluster}' as {role.value}[/green]")


@node_app.command("list")
def node_list(cluster: str) -> None:
    with get_session() as session:
        nodes = list_nodes(session, cluster)
        rows = [(n.host.hostname, n.role.value, n.host.primary_ipv4 or "") for n in nodes]

    table = Table(title=f"Nodes in {cluster}")
    table.add_column("Hostname", style="cyan")
    table.add_column("Role")
    table.add_column("IP")
    for row in rows:
        table.add_row(*row)
    console.print(table)


@node_app.command("remove")
def node_remove(hostname: str, cluster: str, yes: bool = typer.Option(False, "--yes", "-y")) -> None:
    if not yes:
        typer.confirm(f"Remove '{hostname}' from cluster '{cluster}'?", abort=True)
    with get_session() as session:
        ok = remove_node(session, hostname, cluster)
    if ok:
        console.print(f"[green]Removed '{hostname}' from '{cluster}'[/green]")
    else:
        console.print(f"[red]Node not found[/red]")
        raise typer.Exit(1)
```

- [ ] **Step 2: Write `cmdb/cli/generate.py`**

```python
import sys
import typer
from pathlib import Path
from rich.console import Console

from cmdb.db.session import get_session
from cmdb.domain.services.generate import (
    generate_inventory_ini, generate_inventory_yaml, generate_ssh_config
)

app = typer.Typer(help="Generate config files from inventory", no_args_is_help=True)
console = Console()


@app.command("inventory")
def inventory(
    fmt: str = typer.Option("yaml", "--format", "-f", help="yaml or ini"),
    out: str | None = typer.Option(None, "--out", "-o", help="Output file (default: stdout)"),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
) -> None:
    """Generate Ansible inventory."""
    with get_session() as session:
        content = (
            generate_inventory_ini(session, tag=tag)
            if fmt == "ini"
            else generate_inventory_yaml(session, tag=tag)
        )
    if out:
        Path(out).write_text(content)
        console.print(f"[green]Written to {out}[/green]")
    else:
        sys.stdout.write(content)


@app.command("ssh-config")
def ssh_config(
    out: str | None = typer.Option(None, "--out", "-o", help="Output file (default: stdout)"),
    tag: str | None = typer.Option(None, "--tag", "-t", help="Filter by tag"),
) -> None:
    """Generate SSH config blocks."""
    with get_session() as session:
        content = generate_ssh_config(session, tag=tag)
    if out:
        Path(out).write_text(content)
        console.print(f"[green]Written to {out}[/green]")
    else:
        sys.stdout.write(content)
```

- [ ] **Step 3: Smoke-test k8s and generate CLI**

```bash
# k8s
uv run cmdb k8s cluster add homelab-k3s --description "k3s test cluster"
uv run cmdb k8s cluster list
uv run cmdb k8s node add blade14 homelab-k3s --role control-plane
uv run cmdb k8s node list homelab-k3s

# generate
uv run cmdb generate inventory
uv run cmdb generate inventory --format ini
uv run cmdb generate ssh-config
```

Expected: All commands run without error; inventory YAML and SSH config contain blade14.

- [ ] **Step 4: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add cmdb/cli/k8s.py cmdb/cli/generate.py
git commit -m "feat: CLI k8s topology and generate commands"
```

---

> **CLI COMPLETE — natural breakpoint.** Tasks 1–11 deliver a fully working CLI. Tasks 12–21 add the web UI and Docker deployment.

---

## Task 12: FastAPI App and Base Template

**Files:**
- Create: `cmdb/web/app.py`
- Create: `cmdb/web/deps.py`
- Create: `cmdb/web/templates/base.html`

- [ ] **Step 1: Write `cmdb/web/deps.py`**

```python
from pathlib import Path
from fastapi.templating import Jinja2Templates

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
```

- [ ] **Step 2: Write `cmdb/web/app.py`**

```python
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from cmdb.web.routes import dashboard, hosts, import_, k8s, generate, settings


@asynccontextmanager
async def lifespan(app: FastAPI):
    from alembic.config import Config
    from alembic import command
    cfg = Config(str(Path(__file__).parent.parent.parent / "alembic.ini"))
    command.upgrade(cfg, "head")
    yield


app = FastAPI(title="HomeLabCMDB", lifespan=lifespan)
app.include_router(dashboard.router)
app.include_router(hosts.router, prefix="/hosts")
app.include_router(import_.router, prefix="/import")
app.include_router(k8s.router, prefix="/k8s")
app.include_router(generate.router, prefix="/generate")
app.include_router(settings.router, prefix="/settings")
```

- [ ] **Step 3: Write `cmdb/web/templates/base.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{% block title %}HomeLabCMDB{% endblock %}</title>
  <script src="https://unpkg.com/htmx.org@2.0.3" defer></script>
  <style>
    *, *::before, *::after { box-sizing: border-box; }
    body { font-family: system-ui, -apple-system, sans-serif; margin: 0; background: #f0f2f5; color: #1a1a2e; }
    a { color: inherit; text-decoration: none; }
    nav { background: #1a1a2e; color: #e8eaf6; padding: 0 1.5rem; display: flex; align-items: center; height: 52px; gap: 0; }
    nav .brand { font-weight: 700; font-size: 1.1rem; color: white; margin-right: 2rem; }
    nav a { padding: 0 1rem; line-height: 52px; color: #9fa8da; font-size: 0.9rem; }
    nav a:hover, nav a.active { color: white; background: rgba(255,255,255,0.08); }
    main { padding: 1.5rem; max-width: 1280px; margin: 0 auto; }
    h1, h2 { margin: 0 0 1rem; font-weight: 600; }
    .card { background: white; border-radius: 8px; padding: 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,.08); margin-bottom: 1rem; }
    table { width: 100%; border-collapse: collapse; }
    th, td { padding: .65rem 1rem; text-align: left; border-bottom: 1px solid #f0f0f0; font-size: .9rem; }
    th { font-weight: 600; background: #fafafa; color: #555; }
    tr:hover td { background: #fafbff; }
    .badge { display: inline-block; padding: .15rem .5rem; border-radius: 999px; background: #e8eaf6; color: #3730a3; font-size: .78rem; margin: .1rem; }
    .badge-red { background: #fee2e2; color: #991b1b; }
    input[type=search], input[type=text], select { padding: .45rem .75rem; border: 1px solid #ddd; border-radius: 6px; font-size: .9rem; }
    .btn { display: inline-block; padding: .45rem 1rem; background: #3730a3; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: .9rem; }
    .btn:hover { background: #4338ca; }
    .btn-sm { padding: .25rem .65rem; font-size: .82rem; }
    .btn-danger { background: #dc2626; }
    .btn-danger:hover { background: #b91c1c; }
    .btn-secondary { background: #6b7280; }
    .btn-secondary:hover { background: #4b5563; }
    .stats-row { display: flex; gap: 1rem; flex-wrap: wrap; margin-bottom: 1.5rem; }
    .stat-card { flex: 1; min-width: 160px; background: white; border-radius: 8px; padding: 1.25rem 1.5rem; box-shadow: 0 1px 3px rgba(0,0,0,.08); }
    .stat-card .value { font-size: 2rem; font-weight: 700; color: #3730a3; }
    .stat-card .label { font-size: .85rem; color: #6b7280; margin-top: .25rem; }
    pre { background: #f8f9fa; border: 1px solid #e9ecef; border-radius: 6px; padding: 1rem; overflow-x: auto; font-size: .85rem; }
    .flash-success { background: #d1fae5; border: 1px solid #6ee7b7; color: #065f46; padding: .75rem 1rem; border-radius: 6px; margin-bottom: 1rem; }
    .flash-error { background: #fee2e2; border: 1px solid #fca5a5; color: #991b1b; padding: .75rem 1rem; border-radius: 6px; margin-bottom: 1rem; }
  </style>
  {% block head %}{% endblock %}
</head>
<body>
  <nav>
    <span class="brand">HomeLabCMDB</span>
    <a href="/" {% if active == 'dashboard' %}class="active"{% endif %}>Dashboard</a>
    <a href="/hosts" {% if active == 'hosts' %}class="active"{% endif %}>Hosts</a>
    <a href="/k8s" {% if active == 'k8s' %}class="active"{% endif %}>Kubernetes</a>
    <a href="/import" {% if active == 'import' %}class="active"{% endif %}>Import</a>
    <a href="/generate" {% if active == 'generate' %}class="active"{% endif %}>Generate</a>
  </nav>
  <main>
    {% block content %}{% endblock %}
  </main>
</body>
</html>
```

- [ ] **Step 4: Create stub route files so app.py can import them**

Create each file with a minimal router:

`cmdb/web/routes/dashboard.py`:
```python
from fastapi import APIRouter
router = APIRouter()
```

`cmdb/web/routes/hosts.py`:
```python
from fastapi import APIRouter
router = APIRouter()
```

`cmdb/web/routes/import_.py`:
```python
from fastapi import APIRouter
router = APIRouter()
```

`cmdb/web/routes/k8s.py`:
```python
from fastapi import APIRouter
router = APIRouter()
```

`cmdb/web/routes/generate.py`:
```python
from fastapi import APIRouter
router = APIRouter()
```

`cmdb/web/routes/settings.py`:
```python
from fastapi import APIRouter
router = APIRouter()
```

- [ ] **Step 5: Verify app starts**

```bash
uv run cmdb serve &
sleep 2
curl -s http://localhost:8080/ | head -5
kill %1
```

Expected: Returns some response (even 404 is fine at this stage — confirms FastAPI is running).

- [ ] **Step 6: Commit**

```bash
git add cmdb/web/ 
git commit -m "feat: FastAPI app scaffold and base template"
```

---

## Task 13: Dashboard and Hosts List Routes

**Files:**
- Modify: `cmdb/web/routes/dashboard.py`
- Modify: `cmdb/web/routes/hosts.py`
- Create: `cmdb/web/templates/dashboard.html`
- Create: `cmdb/web/templates/hosts/list.html`
- Create: `cmdb/web/templates/hosts/_table.html`  (HTMX partial)
- Create: `tests/test_web.py`

- [ ] **Step 1: Write web tests**

```python
# tests/test_web.py
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from cmdb.domain.models import Base
from cmdb.domain.services.ansible import import_host
from cmdb.web.app import app
from cmdb.web import deps
import tests.conftest as cf


@pytest.fixture(autouse=True)
def override_db(db):
    """Replace the FastAPI DB dependency with the in-memory test session."""
    app.dependency_overrides[deps.get_db_dep] = lambda: db
    yield
    app.dependency_overrides.clear()


@pytest.fixture
def client():
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def populated_client(client, db, blade14_facts):
    import_host(db, blade14_facts)
    return client


def test_dashboard_loads(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "HomeLabCMDB" in r.text


def test_dashboard_shows_host_count(populated_client):
    r = populated_client.get("/")
    assert r.status_code == 200
    assert "1" in r.text  # 1 host


def test_hosts_list_loads(populated_client):
    r = populated_client.get("/hosts")
    assert r.status_code == 200
    assert "blade14" in r.text


def test_hosts_list_search(populated_client):
    r = populated_client.get("/hosts", params={"q": "blade14"})
    assert "blade14" in r.text
    r2 = populated_client.get("/hosts", params={"q": "zzznomatch"})
    assert "blade14" not in r2.text
```

- [ ] **Step 2: Add `get_db_dep` to `cmdb/web/deps.py`**

```python
from pathlib import Path
from fastapi import Depends
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from cmdb.db.session import get_db

TEMPLATES_DIR = Path(__file__).parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def get_db_dep() -> Session:
    yield from get_db()
```

- [ ] **Step 3: Write `cmdb/web/routes/dashboard.py`**

```python
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from sqlalchemy import func

from cmdb.domain.models import Host, K8sCluster, ImportLog
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def dashboard(request: Request, db: Session = Depends(get_db_dep)):
    host_count = db.query(func.count(Host.id)).scalar()
    cluster_count = db.query(func.count(K8sCluster.id)).scalar()
    last_import = db.query(ImportLog).order_by(ImportLog.imported_at.desc()).first()

    os_breakdown: dict[str, int] = {}
    for (family, count) in db.query(Host.os_family, func.count(Host.id)).group_by(Host.os_family).all():
        os_breakdown[family or "Unknown"] = count

    return templates.TemplateResponse(request, "dashboard.html", {
        "active": "dashboard",
        "host_count": host_count,
        "cluster_count": cluster_count,
        "last_import": last_import,
        "os_breakdown": os_breakdown,
    })
```

- [ ] **Step 4: Write `cmdb/web/templates/dashboard.html`**

```html
{% extends "base.html" %}
{% block title %}Dashboard — HomeLabCMDB{% endblock %}
{% block content %}
<h1>Dashboard</h1>
<div class="stats-row">
  <div class="stat-card">
    <div class="value">{{ host_count }}</div>
    <div class="label">Hosts</div>
  </div>
  <div class="stat-card">
    <div class="value">{{ cluster_count }}</div>
    <div class="label">K8s Clusters</div>
  </div>
  <div class="stat-card">
    <div class="value">{{ last_import.imported_at.strftime('%Y-%m-%d') if last_import else '—' }}</div>
    <div class="label">Last Import</div>
  </div>
</div>

{% if os_breakdown %}
<div class="card">
  <h2>OS Breakdown</h2>
  <table>
    <thead><tr><th>OS Family</th><th>Count</th></tr></thead>
    <tbody>
      {% for family, count in os_breakdown.items() %}
      <tr><td>{{ family }}</td><td>{{ count }}</td></tr>
      {% endfor %}
    </tbody>
  </table>
</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 5: Write `cmdb/web/routes/hosts.py`**

```python
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from cmdb.domain.services.hosts import list_hosts
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def hosts_list(
    request: Request,
    q: str = "",
    tag: str = "",
    db: Session = Depends(get_db_dep),
):
    hosts = list_hosts(db, tag=tag or None, os_family=None)
    if q:
        q_lower = q.lower()
        hosts = [h for h in hosts if q_lower in h.hostname.lower()
                 or (h.primary_ipv4 and q_lower in h.primary_ipv4)
                 or (h.os_distribution and q_lower in h.os_distribution.lower())]

    is_htmx = request.headers.get("HX-Request") == "true"
    if is_htmx:
        return templates.TemplateResponse(request, "hosts/_table.html", {"hosts": hosts})
    return templates.TemplateResponse(request, "hosts/list.html", {
        "active": "hosts",
        "hosts": hosts,
        "q": q,
        "tag": tag,
    })
```

- [ ] **Step 6: Write `cmdb/web/templates/hosts/list.html`**

```html
{% extends "base.html" %}
{% block title %}Hosts — HomeLabCMDB{% endblock %}
{% block content %}
<h1>Hosts</h1>
<div class="card" style="margin-bottom:1rem; padding: 1rem 1.5rem;">
  <input
    type="search"
    name="q"
    value="{{ q }}"
    placeholder="Search hostname, IP, OS…"
    hx-get="/hosts"
    hx-trigger="input changed delay:300ms, search"
    hx-target="#hosts-table"
    hx-include="[name='tag']"
    style="width:280px;"
  >
  <input type="hidden" name="tag" value="{{ tag }}">
</div>
<div id="hosts-table">
  {% include "hosts/_table.html" %}
</div>
{% endblock %}
```

- [ ] **Step 7: Write `cmdb/web/templates/hosts/_table.html`**

```html
<table>
  <thead>
    <tr>
      <th>Hostname</th>
      <th>IP</th>
      <th>OS</th>
      <th>CPU</th>
      <th>RAM (MB)</th>
      <th>Tags</th>
      <th>Last Seen</th>
    </tr>
  </thead>
  <tbody>
  {% for h in hosts %}
    <tr style="cursor:pointer" onclick="location.href='/hosts/{{ h.hostname }}'">
      <td><strong>{{ h.hostname }}</strong></td>
      <td>{{ h.primary_ipv4 or '—' }}</td>
      <td>{{ h.os_distribution or '' }} {{ h.os_version or '' }}</td>
      <td>{{ h.cpu_cores or '?' }}c/{{ h.cpu_threads or '?' }}t</td>
      <td>{{ h.memory_mb or '—' }}</td>
      <td>{% for t in h.tags %}<span class="badge">{{ t.name }}</span>{% endfor %}</td>
      <td>{{ h.last_seen.strftime('%Y-%m-%d') if h.last_seen else '—' }}</td>
    </tr>
  {% else %}
    <tr><td colspan="7" style="text-align:center;color:#999;padding:2rem">No hosts found.</td></tr>
  {% endfor %}
  </tbody>
</table>
```

- [ ] **Step 8: Run web tests**

```bash
uv run pytest tests/test_web.py -v
```

Expected: 4 passed.

- [ ] **Step 9: Commit**

```bash
git add cmdb/web/routes/dashboard.py cmdb/web/routes/hosts.py \
        cmdb/web/templates/ cmdb/web/deps.py \
        tests/test_web.py
git commit -m "feat: dashboard and hosts list web routes with HTMX search"
```

---

## Task 14: Host Detail Route

**Files:**
- Modify: `cmdb/web/routes/hosts.py`
- Create: `cmdb/web/templates/hosts/detail.html`
- Create: `cmdb/web/templates/hosts/_tag_list.html`

- [ ] **Step 1: Add tests for host detail and tag operations**

Add to `tests/test_web.py`:

```python
def test_host_detail_loads(populated_client):
    r = populated_client.get("/hosts/blade14")
    assert r.status_code == 200
    assert "blade14" in r.text
    assert "192.168.0.14" in r.text


def test_host_detail_404(client):
    r = client.get("/hosts/ghost")
    assert r.status_code == 404


def test_host_tag_add(populated_client, db):
    r = populated_client.post("/hosts/blade14/tags", data={"tag": "proxmox"})
    assert r.status_code == 200
    assert "proxmox" in r.text


def test_host_tag_remove(populated_client, db):
    from cmdb.domain.services.hosts import add_tag
    add_tag(db, "blade14", "proxmox")
    r = populated_client.delete("/hosts/blade14/tags/proxmox")
    assert r.status_code == 200
    assert "proxmox" not in r.text
```

- [ ] **Step 2: Run new tests — expect failures**

```bash
uv run pytest tests/test_web.py -v
```

- [ ] **Step 3: Extend `cmdb/web/routes/hosts.py` with detail, tag add/remove**

```python
# append to cmdb/web/routes/hosts.py (keep existing imports + hosts_list route)

from fastapi import HTTPException, Form
from fastapi.responses import HTMLResponse
from cmdb.domain.services.hosts import get_host, add_tag, remove_tag
import json


@router.get("/{hostname}")
def host_detail(request: Request, hostname: str, db: Session = Depends(get_db_dep)):
    host = get_host(db, hostname)
    if not host:
        raise HTTPException(status_code=404, detail="Host not found")
    raw_pretty = json.dumps(host.raw_facts, indent=2) if host.raw_facts else ""
    return templates.TemplateResponse(request, "hosts/detail.html", {
        "active": "hosts",
        "host": host,
        "raw_pretty": raw_pretty,
    })


@router.post("/{hostname}/tags")
def host_add_tag(
    request: Request,
    hostname: str,
    tag: str = Form(...),
    db: Session = Depends(get_db_dep),
):
    host = get_host(db, hostname)
    if not host:
        raise HTTPException(status_code=404)
    add_tag(db, hostname, tag)
    host = get_host(db, hostname)
    return templates.TemplateResponse(request, "hosts/_tag_list.html", {
        "host": host,
    })


@router.delete("/{hostname}/tags/{tag_name}")
def host_remove_tag(
    request: Request,
    hostname: str,
    tag_name: str,
    db: Session = Depends(get_db_dep),
):
    host = get_host(db, hostname)
    if not host:
        raise HTTPException(status_code=404)
    remove_tag(db, hostname, tag_name)
    host = get_host(db, hostname)
    return templates.TemplateResponse(request, "hosts/_tag_list.html", {
        "host": host,
    })
```

- [ ] **Step 4: Write `cmdb/web/templates/hosts/_tag_list.html`**

```html
<div id="tag-list">
  {% for t in host.tags %}
  <span class="badge">
    {{ t.name }}
    <button
      class="btn btn-sm btn-danger"
      style="margin-left:.25rem;padding:.1rem .35rem;font-size:.7rem;"
      hx-delete="/hosts/{{ host.hostname }}/tags/{{ t.name }}"
      hx-target="#tag-list"
      hx-swap="outerHTML"
    >×</button>
  </span>
  {% endfor %}
  <form
    hx-post="/hosts/{{ host.hostname }}/tags"
    hx-target="#tag-list"
    hx-swap="outerHTML"
    style="display:inline"
  >
    <input type="text" name="tag" placeholder="add tag" style="width:100px;">
    <button type="submit" class="btn btn-sm">+</button>
  </form>
</div>
```

- [ ] **Step 5: Write `cmdb/web/templates/hosts/detail.html`**

```html
{% extends "base.html" %}
{% block title %}{{ host.hostname }} — HomeLabCMDB{% endblock %}
{% block content %}
<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:1rem;">
  <div>
    <a href="/hosts" style="color:#6b7280;font-size:.9rem;">← Hosts</a>
    <h1 style="margin:.25rem 0 0;">{{ host.hostname }}</h1>
  </div>
</div>

<div style="display:grid; grid-template-columns:1fr 1fr; gap:1rem;">
  <div class="card">
    <h2>Identity</h2>
    <table>
      <tr><th>FQDN</th><td>{{ host.fqdn or '—' }}</td></tr>
      <tr><th>Machine ID</th><td><code>{{ host.machine_id }}</code></td></tr>
      <tr><th>Vendor</th><td>{{ host.system_vendor }} {{ host.product_name }}</td></tr>
      <tr><th>Serial</th><td>{{ host.serial or '—' }}</td></tr>
      <tr><th>Form Factor</th><td>{{ host.form_factor or '—' }}</td></tr>
    </table>
  </div>
  <div class="card">
    <h2>Network</h2>
    <table>
      <tr><th>IP</th><td>{{ host.primary_ipv4 or '—' }}</td></tr>
      <tr><th>Interface</th><td>{{ host.primary_interface or '—' }}</td></tr>
      <tr><th>MAC</th><td>{{ host.primary_mac or '—' }}</td></tr>
      <tr><th>Gateway</th><td>{{ host.gateway or '—' }}</td></tr>
    </table>
  </div>
  <div class="card">
    <h2>OS</h2>
    <table>
      <tr><th>Distribution</th><td>{{ host.os_distribution }} {{ host.os_version }}</td></tr>
      <tr><th>Release</th><td>{{ host.os_release or '—' }}</td></tr>
      <tr><th>Family</th><td>{{ host.os_family or '—' }}</td></tr>
      <tr><th>Kernel</th><td>{{ host.kernel or '—' }}</td></tr>
      <tr><th>Package Mgr</th><td>{{ host.pkg_mgr or '—' }}</td></tr>
    </table>
  </div>
  <div class="card">
    <h2>Hardware</h2>
    <table>
      <tr><th>CPU</th><td>{{ host.cpu_model or '—' }}</td></tr>
      <tr><th>Cores/Threads</th><td>{{ host.cpu_cores }}c / {{ host.cpu_threads }}t</td></tr>
      <tr><th>RAM</th><td>{{ host.memory_mb }} MB</td></tr>
      <tr><th>Arch</th><td>{{ host.arch or '—' }}</td></tr>
    </table>
  </div>
  <div class="card">
    <h2>Security</h2>
    <table>
      <tr><th>AppArmor</th><td>{{ host.apparmor_status or '—' }}</td></tr>
      <tr><th>SELinux</th><td>{{ host.selinux_status or '—' }}</td></tr>
      <tr><th>FIPS</th><td>{{ host.fips or '—' }}</td></tr>
    </table>
  </div>
  <div class="card">
    <h2>Virtualization</h2>
    <table>
      <tr><th>Type</th><td>{{ host.virt_type or '—' }}</td></tr>
      <tr><th>Role</th><td>{{ host.virt_role or '—' }}</td></tr>
    </table>
  </div>
</div>

<div class="card" style="margin-top:1rem;">
  <h2>Tags</h2>
  {% include "hosts/_tag_list.html" %}
</div>

<details style="margin-top:1rem;">
  <summary style="cursor:pointer;font-weight:600;padding:.75rem;background:white;border-radius:8px;">
    Raw Ansible Facts
  </summary>
  <div class="card" style="margin-top:.5rem;">
    <pre>{{ raw_pretty }}</pre>
  </div>
</details>
{% endblock %}
```

- [ ] **Step 6: Run all web tests**

```bash
uv run pytest tests/test_web.py -v
```

Expected: All 8 tests pass.

- [ ] **Step 7: Commit**

```bash
git add cmdb/web/routes/hosts.py cmdb/web/templates/hosts/
git commit -m "feat: host detail page with inline HTMX tag management"
```

---

## Task 15: Import Route

**Files:**
- Modify: `cmdb/web/routes/import_.py`
- Create: `cmdb/web/templates/import/index.html`

- [ ] **Step 1: Add import web tests to `tests/test_web.py`**

```python
def test_import_page_loads(client):
    r = client.get("/import")
    assert r.status_code == 200
    assert "Import" in r.text


def test_import_upload_single_file(client, db, blade14_facts):
    import json
    content = json.dumps(blade14_facts).encode()
    r = client.post(
        "/import/upload",
        files={"files": ("blade14", content, "application/json")},
    )
    assert r.status_code == 200
    assert "1" in r.text  # 1 upserted
```

- [ ] **Step 2: Write `cmdb/web/routes/import_.py`**

```python
import json
import tempfile
from pathlib import Path

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from cmdb.domain.models import ImportLog, ImportSource
from cmdb.domain.services.ansible import import_from_path
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def import_page(request: Request, db: Session = Depends(get_db_dep)):
    logs = db.query(ImportLog).order_by(ImportLog.imported_at.desc()).limit(20).all()
    return templates.TemplateResponse(request, "import/index.html", {
        "active": "import",
        "logs": logs,
    })


@router.post("/upload")
async def import_upload(
    request: Request,
    db: Session = Depends(get_db_dep),
    files: list[UploadFile] = File(...),
):
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        for f in files:
            dest = tmp_path / (f.filename or "upload.json")
            dest.write_bytes(await f.read())
        log = import_from_path(db, tmp, ImportSource.WEB)

    return templates.TemplateResponse(request, "import/index.html", {
        "active": "import",
        "logs": db.query(ImportLog).order_by(ImportLog.imported_at.desc()).limit(20).all(),
        "last_result": log,
    })
```

- [ ] **Step 3: Write `cmdb/web/templates/import/index.html`**

```html
{% extends "base.html" %}
{% block title %}Import — HomeLabCMDB{% endblock %}
{% block content %}
<h1>Import Ansible Facts</h1>

{% if last_result %}
<div class="flash-{{ 'success' if last_result.hosts_failed == 0 else 'error' }}">
  Imported: {{ last_result.hosts_upserted }} upserted, {{ last_result.hosts_failed }} failed.
  {% if last_result.notes %}<pre style="margin:.5rem 0 0;font-size:.8rem;">{{ last_result.notes }}</pre>{% endif %}
</div>
{% endif %}

<div class="card">
  <h2>Upload Files</h2>
  <p style="color:#6b7280;font-size:.9rem;">Upload one or more JSON files from <code>ansible -m setup --tree out/ &lt;hosts&gt;</code></p>
  <form
    hx-post="/import/upload"
    hx-target="body"
    hx-swap="outerHTML"
    enctype="multipart/form-data"
  >
    <input type="file" name="files" multiple accept=".json" style="margin-bottom:.75rem;display:block;">
    <button type="submit" class="btn">Import</button>
  </form>
</div>

<div class="card">
  <h2>Import History</h2>
  <table>
    <thead><tr><th>Date</th><th>Source</th><th>File</th><th>Upserted</th><th>Failed</th></tr></thead>
    <tbody>
    {% for log in logs %}
    <tr>
      <td>{{ log.imported_at.strftime('%Y-%m-%d %H:%M') }}</td>
      <td>{{ log.source.value }}</td>
      <td style="font-size:.85rem;color:#6b7280;">{{ log.filename or '—' }}</td>
      <td>{{ log.hosts_upserted }}</td>
      <td>{% if log.hosts_failed %}<span class="badge badge-red">{{ log.hosts_failed }}</span>{% else %}0{% endif %}</td>
    </tr>
    {% else %}
    <tr><td colspan="5" style="text-align:center;color:#999;padding:2rem">No imports yet.</td></tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 4: Run tests**

```bash
uv run pytest tests/test_web.py -v
```

Expected: All tests pass.

- [ ] **Step 5: Commit**

```bash
git add cmdb/web/routes/import_.py cmdb/web/templates/import/
git commit -m "feat: web import route with file upload and history"
```

---

## Task 16: K8s and Generate Routes

**Files:**
- Modify: `cmdb/web/routes/k8s.py`
- Modify: `cmdb/web/routes/generate.py`
- Create: `cmdb/web/templates/k8s/index.html`
- Create: `cmdb/web/templates/generate/index.html`

- [ ] **Step 1: Write `cmdb/web/routes/k8s.py`**

```python
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from cmdb.domain.models import K8sNodeRole
from cmdb.domain.services.k8s import (
    add_cluster, add_node, delete_cluster,
    list_clusters, list_nodes, remove_node,
)
from cmdb.domain.services.hosts import list_hosts
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def k8s_page(request: Request, db: Session = Depends(get_db_dep)):
    clusters = list_clusters(db)
    all_hosts = list_hosts(db)
    return templates.TemplateResponse(request, "k8s/index.html", {
        "active": "k8s",
        "clusters": clusters,
        "all_hosts": all_hosts,
        "roles": [r.value for r in K8sNodeRole],
    })


@router.post("/clusters")
def create_cluster(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    db: Session = Depends(get_db_dep),
):
    add_cluster(db, name, description or None)
    return RedirectResponse("/k8s", status_code=303)


@router.post("/clusters/{name}/delete")
def delete_cluster_route(name: str, db: Session = Depends(get_db_dep)):
    delete_cluster(db, name)
    return RedirectResponse("/k8s", status_code=303)


@router.post("/nodes")
def add_node_route(
    hostname: str = Form(...),
    cluster: str = Form(...),
    role: str = Form(...),
    db: Session = Depends(get_db_dep),
):
    add_node(db, hostname, cluster, K8sNodeRole(role))
    return RedirectResponse("/k8s", status_code=303)


@router.post("/nodes/{hostname}/{cluster}/delete")
def remove_node_route(hostname: str, cluster: str, db: Session = Depends(get_db_dep)):
    remove_node(db, hostname, cluster)
    return RedirectResponse("/k8s", status_code=303)
```

- [ ] **Step 2: Write `cmdb/web/templates/k8s/index.html`**

```html
{% extends "base.html" %}
{% block title %}Kubernetes — HomeLabCMDB{% endblock %}
{% block content %}
<h1>Kubernetes Topology</h1>

<div style="display:grid; grid-template-columns:2fr 1fr; gap:1rem; align-items:start;">
  <div>
    {% for cluster in clusters %}
    <div class="card">
      <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem;">
        <h2 style="margin:0;">{{ cluster.name }}</h2>
        <form method="post" action="/k8s/clusters/{{ cluster.name }}/delete" style="display:inline">
          <button class="btn btn-sm btn-danger" onclick="return confirm('Delete cluster?')">Delete</button>
        </form>
      </div>
      {% if cluster.description %}<p style="color:#6b7280;margin:0 0 1rem;">{{ cluster.description }}</p>{% endif %}
      <table>
        <thead><tr><th>Host</th><th>IP</th><th>Role</th><th></th></tr></thead>
        <tbody>
        {% for node in cluster.nodes %}
        <tr>
          <td><a href="/hosts/{{ node.host.hostname }}">{{ node.host.hostname }}</a></td>
          <td>{{ node.host.primary_ipv4 or '—' }}</td>
          <td><span class="badge">{{ node.role.value }}</span></td>
          <td>
            <form method="post" action="/k8s/nodes/{{ node.host.hostname }}/{{ cluster.name }}/delete">
              <button class="btn btn-sm btn-danger">Remove</button>
            </form>
          </td>
        </tr>
        {% else %}
        <tr><td colspan="4" style="color:#999;text-align:center">No nodes yet.</td></tr>
        {% endfor %}
        </tbody>
      </table>
      <form method="post" action="/k8s/nodes" style="margin-top:1rem;display:flex;gap:.5rem;align-items:center;flex-wrap:wrap;">
        <input type="hidden" name="cluster" value="{{ cluster.name }}">
        <select name="hostname" style="padding:.4rem .75rem;border:1px solid #ddd;border-radius:6px;">
          {% for h in all_hosts %}<option value="{{ h.hostname }}">{{ h.hostname }}</option>{% endfor %}
        </select>
        <select name="role" style="padding:.4rem .75rem;border:1px solid #ddd;border-radius:6px;">
          {% for r in roles %}<option value="{{ r }}">{{ r }}</option>{% endfor %}
        </select>
        <button type="submit" class="btn btn-sm">Add Node</button>
      </form>
    </div>
    {% else %}
    <div class="card" style="color:#999;text-align:center;padding:2rem;">No clusters yet. Create one →</div>
    {% endfor %}
  </div>

  <div class="card">
    <h2>New Cluster</h2>
    <form method="post" action="/k8s/clusters" style="display:flex;flex-direction:column;gap:.75rem;">
      <input type="text" name="name" placeholder="Cluster name" required>
      <input type="text" name="description" placeholder="Description (optional)">
      <button type="submit" class="btn">Create</button>
    </form>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 3: Write `cmdb/web/routes/generate.py`**

```python
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from cmdb.domain.services.generate import (
    generate_inventory_ini, generate_inventory_yaml, generate_ssh_config
)
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def generate_page(
    request: Request,
    fmt: str = "yaml",
    tag: str = "",
    db: Session = Depends(get_db_dep),
):
    inv_yaml = generate_inventory_yaml(db, tag=tag or None)
    inv_ini = generate_inventory_ini(db, tag=tag or None)
    ssh = generate_ssh_config(db, tag=tag or None)
    return templates.TemplateResponse(request, "generate/index.html", {
        "active": "generate",
        "inv_yaml": inv_yaml,
        "inv_ini": inv_ini,
        "ssh_config": ssh,
        "tag": tag,
    })


@router.get("/download/inventory.yaml")
def download_inventory_yaml(tag: str = "", db: Session = Depends(get_db_dep)):
    content = generate_inventory_yaml(db, tag=tag or None)
    return Response(content, media_type="text/plain",
                    headers={"Content-Disposition": "attachment; filename=inventory.yaml"})


@router.get("/download/inventory.ini")
def download_inventory_ini(tag: str = "", db: Session = Depends(get_db_dep)):
    content = generate_inventory_ini(db, tag=tag or None)
    return Response(content, media_type="text/plain",
                    headers={"Content-Disposition": "attachment; filename=hosts.ini"})


@router.get("/download/ssh-config")
def download_ssh_config(tag: str = "", db: Session = Depends(get_db_dep)):
    content = generate_ssh_config(db, tag=tag or None)
    return Response(content, media_type="text/plain",
                    headers={"Content-Disposition": "attachment; filename=ssh-config"})
```

- [ ] **Step 4: Write `cmdb/web/templates/generate/index.html`**

```html
{% extends "base.html" %}
{% block title %}Generate — HomeLabCMDB{% endblock %}
{% block content %}
<h1>Generate Configs</h1>

<div class="card" style="margin-bottom:1rem;padding:1rem 1.5rem;">
  <form style="display:flex;gap:.75rem;align-items:center;">
    <label style="font-size:.9rem;">Filter by tag:</label>
    <input type="text" name="tag" value="{{ tag }}" placeholder="e.g. proxmox">
    <button type="submit" class="btn btn-sm">Apply</button>
    {% if tag %}<a href="/generate" class="btn btn-sm btn-secondary">Clear</a>{% endif %}
  </form>
</div>

<div style="display:grid; grid-template-columns:1fr 1fr 1fr; gap:1rem;">
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.75rem;">
      <h2 style="margin:0;">Inventory YAML</h2>
      <a href="/generate/download/inventory.yaml{% if tag %}?tag={{ tag }}{% endif %}" class="btn btn-sm">Download</a>
    </div>
    <pre>{{ inv_yaml }}</pre>
  </div>
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.75rem;">
      <h2 style="margin:0;">Inventory INI</h2>
      <a href="/generate/download/inventory.ini{% if tag %}?tag={{ tag }}{% endif %}" class="btn btn-sm">Download</a>
    </div>
    <pre>{{ inv_ini }}</pre>
  </div>
  <div class="card">
    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:.75rem;">
      <h2 style="margin:0;">SSH Config</h2>
      <a href="/generate/download/ssh-config{% if tag %}?tag={{ tag }}{% endif %}" class="btn btn-sm">Download</a>
    </div>
    <pre>{{ ssh_config }}</pre>
  </div>
</div>
{% endblock %}
```

- [ ] **Step 5: Add stub `cmdb/web/routes/settings.py`**

```python
from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session
from cmdb.config import settings
from cmdb.web.deps import templates, get_db_dep

router = APIRouter()


@router.get("/")
def settings_page(request: Request):
    return templates.TemplateResponse(request, "dashboard.html", {
        "active": "settings",
        "host_count": 0, "cluster_count": 0, "last_import": None, "os_breakdown": {},
    })
```

(Settings page reuses dashboard template as a placeholder — expand later as needed.)

- [ ] **Step 6: Run full test suite**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 7: Manual smoke test of web UI**

```bash
uv run cmdb serve &
# Open http://localhost:8080 in browser
# Navigate: Dashboard → Hosts → host detail → Import → K8s → Generate
kill %1
```

Expected: All pages load without errors.

- [ ] **Step 8: Commit**

```bash
git add cmdb/web/routes/ cmdb/web/templates/
git commit -m "feat: k8s topology, generate, and import web routes"
```

---

## Task 17: Dockerfile and Docker Compose

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`
- Create: `.dockerignore`
- Create: `.gitignore`

- [ ] **Step 1: Write `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.venv/
*.db
*.db-shm
*.db-wal
dist/
.superpowers/
```

- [ ] **Step 2: Write `.dockerignore`**

```
.venv/
__pycache__/
*.pyc
*.db
.git/
tests/
docs/
.superpowers/
```

- [ ] **Step 3: Write `Dockerfile`**

```dockerfile
FROM python:3.12-slim

RUN pip install uv

WORKDIR /app
COPY pyproject.toml .
RUN uv sync --no-dev --compile-bytecode

COPY cmdb/ cmdb/
COPY alembic.ini .

ENV CMDB_DB_PATH=/data/cmdb.db
ENV CMDB_HOST=0.0.0.0
ENV CMDB_PORT=8080

EXPOSE 8080

ENTRYPOINT ["uv", "run", "cmdb", "serve"]
```

- [ ] **Step 4: Write `docker-compose.yml`**

```yaml
services:
  cmdb:
    build: .
    ports:
      - "8080:8080"
    volumes:
      - ./data:/data
    environment:
      - CMDB_DB_PATH=/data/cmdb.db
    restart: unless-stopped
```

- [ ] **Step 5: Build and test the Docker image**

```bash
mkdir -p data
docker build -t homelab-cmdb .
docker run --rm -v "$(pwd)/data:/data" -p 8080:8080 homelab-cmdb &
sleep 3
curl -s http://localhost:8080/ | grep -c "HomeLabCMDB"
docker stop $(docker ps -q --filter ancestor=homelab-cmdb)
```

Expected: `grep` returns `1` — the page loaded.

- [ ] **Step 6: Verify DB persists across container restart**

```bash
# Start container, import a host via CLI into the volume DB
CMDB_DB_PATH=$(pwd)/data/cmdb.db uv run cmdb import ansible tests/fixtures/blade14.json
# Start container and check host appears
docker run --rm -v "$(pwd)/data:/data" -p 8080:8080 homelab-cmdb &
sleep 3
curl -s http://localhost:8080/hosts | grep blade14
docker stop $(docker ps -q --filter ancestor=homelab-cmdb)
```

Expected: `blade14` appears in the response.

- [ ] **Step 7: Commit**

```bash
git add Dockerfile docker-compose.yml .dockerignore .gitignore
git commit -m "feat: Docker deployment with volume-mounted DB"
```

---

## Task 18: Final Wiring and CLAUDE.md

**Files:**
- Create: `HomeLabCMDB/CLAUDE.md`

- [ ] **Step 1: Run full test suite and verify clean**

```bash
uv run pytest tests/ -v
```

Expected: All tests pass.

- [ ] **Step 2: Run a full end-to-end smoke test**

```bash
# Clean slate
rm -f cmdb.db

# Apply migrations
uv run cmdb db upgrade

# Import ansible facts
uv run cmdb import ansible tests/fixtures/blade14.json

# Verify host list
uv run cmdb hosts list

# Tag a host
uv run cmdb hosts tag blade14 proxmox

# Generate inventory — verify it parses
uv run cmdb generate inventory | uv run python -c "import sys,yaml; yaml.safe_load(sys.stdin); print('valid YAML')"

# Generate SSH config
uv run cmdb generate ssh-config

# K8s
uv run cmdb k8s cluster add homelab-k3s
uv run cmdb k8s node add blade14 homelab-k3s --role control-plane
uv run cmdb k8s node list homelab-k3s
```

Expected: All commands succeed; inventory YAML is valid; `blade14` appears as control-plane.

- [ ] **Step 3: Write `CLAUDE.md`**

```markdown
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

All business logic in `cmdb/domain/services/`. CLI (`cmdb/cli/`) and Web (`cmdb/web/`) are thin shells.
Upsert key: `machine_id` (from `ansible_machine_id`). Never changes across imports.

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
```

- [ ] **Step 4: Final commit**

```bash
git add CLAUDE.md
git commit -m "docs: CLAUDE.md and project README"
```

---

## Verification Checklist

- [ ] `uv run pytest tests/ -v` — all tests pass
- [ ] `uv run cmdb import ansible tests/fixtures/blade14.json` — 1 upserted, 0 failed
- [ ] Re-run import — still 1 host in DB (upsert works)
- [ ] `uv run cmdb generate inventory | python -c "import sys,yaml; yaml.safe_load(sys.stdin)"` — valid YAML
- [ ] `uv run cmdb generate ssh-config` — contains `Host blade14`
- [ ] `uv run cmdb serve` → browse to `http://localhost:8080` — dashboard loads
- [ ] Upload `tests/fixtures/blade14.json` via web UI — ImportLog entry created
- [ ] Add k8s cluster and assign node via web UI — appears on `/k8s`
- [ ] `docker compose up` — web UI accessible, DB persists after container restart
