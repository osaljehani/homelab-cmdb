"""Parse `ss -tulpn` output and store a host's listening ports (replace-on-import)."""

import re
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from cmdb.domain.models import Host, ListeningPort

# Pull the first process name from ss's `users:(("name",pid=..,fd=..),...)` column.
_PROCESS_RE = re.compile(r'\(\("([^"]+)"')


def parse_ss(text: str) -> list[dict[str, Any]]:
    """Parse headerless `ss -tulpn` lines into listener dicts.

    Columns: Netid State Recv-Q Send-Q Local-Address:Port Peer Process. The process
    column is absent when ss runs without privilege to see the owning process.
    """
    rows: list[dict[str, Any]] = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        proto = parts[0].lower()
        if proto not in ("tcp", "udp"):
            continue
        addr, _, port = parts[4].rpartition(":")
        if not port.isdigit():
            continue
        addr = addr.strip("[]") or "*"
        m = _PROCESS_RE.search(line)
        rows.append({
            "proto": proto,
            "address": addr,
            "port": int(port),
            "process": m.group(1) if m else None,
        })
    return rows


def import_ports(session: Session, data: dict[str, Any]) -> dict[str, Any]:
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
        raise ValueError(f"Host '{hostname}' not found   import it via Ansible first")

    session.query(ListeningPort).filter_by(host_id=host.id).delete()
    upserted = 0
    for p in data.get("ports", []):
        session.add(ListeningPort(host_id=host.id, **p))
        upserted += 1

    session.flush()
    return {"ports": upserted, "errors": []}
