import yaml
from sqlalchemy.orm import Session

from cmdb.config import settings
from cmdb.domain.models import Host, Tag

# Applied as ansible_ssh_common_args when CMDB_ANSIBLE_SSH_ARGS is unset. Matches the
# behavior of the hand-maintained inventory: don't choke on first-connect host keys.
DEFAULT_SSH_COMMON_ARGS = "-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"


def _get_hosts(session: Session, tag: str | None = None) -> list[Host]:
    q = session.query(Host)
    if tag:
        q = q.filter(Host.tags.any(Tag.name == tag.lower().strip()))
    return q.order_by(Host.hostname).all()


def _ssh_vars() -> dict:
    """Build an Ansible ``all.vars`` block from config; omit anything unset."""
    vars_block: dict = {}
    if settings.ansible_user:
        vars_block["ansible_user"] = settings.ansible_user
    if settings.ssh_private_key:
        vars_block["ansible_ssh_private_key_file"] = settings.ssh_private_key
    # None -> default; explicit "" disables; any other value is used verbatim.
    ssh_args = DEFAULT_SSH_COMMON_ARGS if settings.ansible_ssh_args is None \
        else settings.ansible_ssh_args
    if ssh_args:
        vars_block["ansible_ssh_common_args"] = ssh_args
    return vars_block


def generate_inventory_yaml(
    session: Session, tag: str | None = None, include_ssh_vars: bool = False
) -> str:
    hosts = _get_hosts(session, tag)
    inventory: dict = {"all": {"hosts": {}}}
    if include_ssh_vars:
        vars_block = _ssh_vars()
        if vars_block:
            inventory["all"]["vars"] = vars_block
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
