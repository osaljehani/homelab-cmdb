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
