import json
from pathlib import Path
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from cmdb.domain.models import Container, Host, ImportLog, ImportSource
from cmdb.domain.services.vuln_snapshots import write_daily_snapshot


def _parse_labels(raw: Any) -> dict[str, str]:
    """Parse docker-style label strings ('k=v,k2=v2') or dicts into a dict."""
    if isinstance(raw, dict):
        return {str(k): str(v) for k, v in raw.items()}
    if isinstance(raw, str) and raw:
        out: dict[str, str] = {}
        for part in raw.split(","):
            if "=" in part:
                k, v = part.split("=", 1)
                out[k.strip()] = v.strip()
        return out
    return {}


def _normalise_container(raw: dict[str, Any]) -> dict[str, Any]:
    """Map either the CMDB schema or raw `docker ps --format '{{json .}}'` keys."""
    labels = _parse_labels(raw.get("Labels"))
    compose = (
        raw.get("compose_project")
        or raw.get("Project")
        or labels.get("com.docker.compose.project")
    )
    ports = raw.get("ports") or raw.get("Ports")
    if isinstance(ports, list):
        ports = ", ".join(str(p) for p in ports)
    return {
        "name": raw.get("name") or raw.get("Names") or raw.get("Name"),
        "image": raw.get("image") or raw.get("Image"),
        "status": raw.get("status") or raw.get("Status"),
        "state": raw.get("state") or raw.get("State"),
        "ports": ports,
        "compose_project": compose,
    }


def import_containers(session: Session, data: dict[str, Any]) -> dict[str, Any]:
    """Replace-on-import: all containers for the host are rewritten from `data`."""
    hostname = data.get("host") or data.get("hostname")
    if not hostname:
        raise ValueError("'host' key is required")

    name_lower = hostname.lower()
    host = (
        session.query(Host)
        .filter(
            (func.lower(Host.hostname) == name_lower)
            | (func.lower(Host.fqdn) == name_lower)
        )
        .first()
    )
    if not host:
        raise ValueError(f"Host '{hostname}' not found import it via Ansible first")

    # Replace: drop existing containers for this host, then re-insert.
    session.query(Container).filter_by(host_id=host.id).delete()

    containers_raw = data.get("containers", [])
    upserted = 0
    for raw in containers_raw:
        fields = _normalise_container(raw)
        if not fields["name"]:
            continue
        session.add(Container(host_id=host.id, **fields))
        upserted += 1

    session.flush()
    return {"containers": upserted, "errors": []}


def import_from_path(session: Session, path: str, source: ImportSource) -> ImportLog:
    target = Path(path)
    files = (
        [target] if target.is_file() else [f for f in target.iterdir() if f.is_file()]
    )

    total = 0
    all_errors: list[str] = []

    for f in files:
        try:
            data = json.loads(f.read_text())
        except Exception as exc:
            all_errors.append(f"{f.name}: JSON parse error: {exc}")
            continue

        records = data if isinstance(data, list) else [data]

        for i, record in enumerate(records):
            label = f"{f.name}[{i}]" if i else f.name
            try:
                counts = import_containers(session, record)
                total += counts["containers"]
            except Exception as exc:
                all_errors.append(f"{label}: {exc}")

    log = ImportLog(
        source=source,
        filename=str(path),
        hosts_upserted=0,
        hosts_failed=0,
        containers_upserted=total,
        notes="\n".join(all_errors) or None,
    )
    session.add(log)
    # Placements changed: refresh today's vuln snapshot so the dashboard trend
    # reflects the new running set immediately (past days stay frozen).
    write_daily_snapshot(session)
    session.flush()
    return log
