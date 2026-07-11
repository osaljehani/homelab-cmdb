"""Seed a fresh database with a fictional demo fleet.

Runs the real import services against fixture payloads under ``cmdb/demo/data/``
so the demo exercises the same code paths as a real import (upserts, snapshot
history, tag application, freshness). See ``cmdb/demo/data/`` for the fixture
JSON. All hostnames/IPs/MACs/machine_ids are synthetic (RFC 5737 IPs,
``example.lan`` domain, ``02:00:5e:xx:xx:xx`` MACs, ``100.64.0.0/10`` tailnet).
"""

import importlib.resources
import json
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from cmdb.domain.models import Host, HostSnapshot, Image, ImportLog, ImportSource
from cmdb.domain.services import docker_import, hosts, k8s_import
from cmdb.domain.services import ansible as ansible_import
from cmdb.domain.services import trivy_import
from cmdb.domain.services.ports_import import import_ports
from cmdb.domain.services.tailscale_import import import_tailscale

# Hosts get tags applied at the end of the seed.
_TAGS: dict[str, list[str]] = {
    "atlas": ["docker", "prod"],
    "hydra": ["k8s", "prod"],
    "orion": ["k8s"],
    "lyra": ["k8s"],
    "vega": ["docker"],
    "rho": ["backup"],
}

# Image (canonical ref, matched via startswith on the trivy ArtifactName) that
# gets flagged as expected_noisy at the end of the seed.
_NOISY_IMAGE_REF = "grafana/grafana:11.2.0"

# rho is deliberately left stale (CMDB_STALE_DAYS defaults to 7).
_STALE_HOSTNAME = "rho"


def _data_dir(*parts: str) -> str:
    path = importlib.resources.files("cmdb.demo") / "data"
    for part in parts:
        path = path / part
    return str(path)


def _backdate_initial_import(session: Session) -> None:
    """Push the first snapshot + host creation back ~30 days.

    Makes the demo fleet look like it has been tracked for a while, and gives
    the run1 -> run2 history a believable time gap.
    """
    backdated = datetime.utcnow() - timedelta(days=30)
    for host in session.query(Host).all():
        host.created_at = backdated
    for snap in session.query(HostSnapshot).all():
        snap.captured_at = backdated
    session.flush()


def _seed_tailscale_and_ports(session: Session) -> None:
    ts_path = importlib.resources.files("cmdb.demo") / "data" / "tailscale.json"
    ports_path = importlib.resources.files("cmdb.demo") / "data" / "ports.json"

    ts_records = json.loads(ts_path.read_text())
    ports_records = json.loads(ports_path.read_text())

    for record in ts_records:
        import_tailscale(session, record)
    for record in ports_records:
        import_ports(session, record)

    log = ImportLog(
        source=ImportSource.CLI,
        filename="demo:tailscale+ports",
        hosts_upserted=0,
        hosts_failed=0,
        tailscale_services_upserted=sum(len(r.get("services", [])) for r in ts_records),
        listening_ports_upserted=sum(len(r.get("ports", [])) for r in ports_records),
    )
    session.add(log)
    session.flush()


def _seed_trivy(session: Session) -> None:
    trivy_dir = importlib.resources.files("cmdb.demo") / "data" / "trivy"
    now = datetime.utcnow()
    for i, f in enumerate(sorted(trivy_dir.iterdir())):
        if not f.is_file():
            continue
        envelope = json.loads(f.read_text())
        # Rewrite scanned_at to a recent relative timestamp so the demo never
        # looks frozen in the past no matter when it's run.
        envelope["scanned_at"] = (now - timedelta(days=1 + i)).isoformat() + "Z"

        log = ImportLog(
            source=ImportSource.CLI,
            filename=f"demo:trivy:{f.name}",
            hosts_upserted=0,
            hosts_failed=0,
        )
        session.add(log)
        session.flush()

        counts = trivy_import.import_scan_run(session, envelope, import_log_id=log.id)
        log.images_scanned = counts["images"]
        log.vulnerabilities_upserted = counts["vulnerabilities"]
        log.notes = "\n".join(counts["errors"]) or None
        session.flush()


def _apply_tags(session: Session) -> None:
    for hostname, tags in _TAGS.items():
        for tag in tags:
            hosts.add_tag(session, hostname, tag)
    session.flush()


def _finalize_freshness(session: Session) -> None:
    now = datetime.utcnow()
    stale_at = now - timedelta(days=10)
    for host in session.query(Host).all():
        host.last_seen = stale_at if host.hostname == _STALE_HOSTNAME else now
    session.flush()


def _mark_noisy_image(session: Session) -> None:
    image = session.query(Image).filter(Image.ref == _NOISY_IMAGE_REF).first()
    if image is not None:
        image.expected_noisy = True
        session.flush()


def seed(session: Session) -> None:
    """Populate an empty database with the fictional demo fleet.

    Caller is responsible for checking the DB is empty first (see
    ``cmdb demo-seed``) — this function does not guard against re-seeding an
    already-populated database and will raise on unique-constraint violations.
    """
    # 1. First ansible run establishes the hosts.
    ansible_import.import_from_path(
        session, _data_dir("ansible", "run1"), ImportSource.CLI
    )
    session.flush()

    # 2. Backdate so the fleet looks established, before the second run adds
    #    "recent" history on top.
    _backdate_initial_import(session)

    # 3. Second ansible run — a handful of hosts have different facts, which
    #    produces real change-history diffs.
    ansible_import.import_from_path(
        session, _data_dir("ansible", "run2"), ImportSource.CLI
    )

    # 4. Docker containers (host must already exist from step 1).
    docker_import.import_from_path(session, _data_dir("docker"), ImportSource.CLI)

    # 5. K8s cluster topology + workloads.
    k8s_import.import_from_path(session, _data_dir("k8s"), ImportSource.CLI)

    # 6. Tailscale + listening ports (no import_from_path for these).
    _seed_tailscale_and_ports(session)

    # 7. Trivy vulnerability scans.
    _seed_trivy(session)

    # 8. Tags.
    _apply_tags(session)

    # 9. Freshness (rho goes stale) + one expected-noisy image.
    _finalize_freshness(session)
    _mark_noisy_image(session)

    session.commit()
