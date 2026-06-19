"""Parse `tailscale status`/`serve status` JSON and store a host's Tailscale state.

The serve-status JSON shape varies across Tailscale versions, so the serve parser is
deliberately defensive: it extracts what it recognises and returns an empty service
list for anything it does not, rather than raising.
"""

import json
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from cmdb.domain.models import Host, TailscaleService


def _port_of(hostport: str) -> int | None:
    tail = str(hostport).rsplit(":", 1)[-1]
    return int(tail) if tail.isdigit() else None


def _parse_self(status_json: str) -> dict[str, Any]:
    if not status_json or not status_json.strip():
        return {}
    try:
        self_node = (json.loads(status_json) or {}).get("Self") or {}
    except (ValueError, AttributeError):
        return {}
    ips = self_node.get("TailscaleIPs") or []
    ipv4 = next((ip for ip in ips if ":" not in ip), None)
    tags = self_node.get("Tags") or []
    dns = (self_node.get("DNSName") or "").rstrip(".")
    return {
        "ipv4": ipv4,
        "dns_name": dns or None,
        "tags": ",".join(tags) if tags else None,
        "exit_node": bool(self_node.get("ExitNodeOption")),
        "online": bool(self_node.get("Online")),
    }


def _parse_serve(serve_json: str) -> list[dict[str, Any]]:
    if not serve_json or not serve_json.strip():
        return []
    try:
        cfg = json.loads(serve_json)
    except ValueError:
        return []
    if not isinstance(cfg, dict):
        return []
    funnel_map = cfg.get("AllowFunnel") or {}
    services: list[dict[str, Any]] = []
    for hostport, conf in (cfg.get("Web") or {}).items():
        port = _port_of(hostport)
        funnel = bool(funnel_map.get(hostport))
        for path, handler in ((conf or {}).get("Handlers") or {}).items():
            handler = handler or {}
            target = handler.get("Proxy") or handler.get("Path") or path
            services.append({"proto": "https", "port": port,
                             "target": target, "funnel": funnel})
    return services


def parse_tailscale_status(status_json: str, serve_json: str) -> dict[str, Any]:
    return {"self": _parse_self(status_json), "services": _parse_serve(serve_json)}


def import_tailscale(session: Session, data: dict[str, Any]) -> dict[str, Any]:
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

    self_node = data.get("self") or {}
    host.tailscale_ipv4 = self_node.get("ipv4")
    host.tailscale_dns_name = self_node.get("dns_name")
    host.tailscale_tags = self_node.get("tags")
    host.tailscale_exit_node = self_node.get("exit_node")
    host.tailscale_online = self_node.get("online")

    session.query(TailscaleService).filter_by(host_id=host.id).delete()
    upserted = 0
    for svc in data.get("services", []):
        session.add(TailscaleService(host_id=host.id, **svc))
        upserted += 1

    session.flush()
    return {"hosts": 1, "services": upserted, "errors": []}
