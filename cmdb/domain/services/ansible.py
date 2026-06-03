import json
import re
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


_HOST_SEPARATOR = re.compile(r"^[\w\-\.]+ \| \w+ =>", re.MULTILINE)


def _parse_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text().strip()
    separators = list(_HOST_SEPARATOR.finditer(text))
    if not separators:
        # Plain JSON file (from --tree)
        return [json.loads(text)]
    # Multi-host stdout: split on each "hostname | STATUS =>" boundary
    blocks = []
    for i, match in enumerate(separators):
        start = text.index("=>", match.start()) + 2
        end = separators[i + 1].start() if i + 1 < len(separators) else len(text)
        blocks.append(json.loads(text[start:end].strip()))
    return blocks


def import_from_path(session: Session, path: str, source: ImportSource) -> ImportLog:
    target = Path(path)
    files = [target] if target.is_file() else [f for f in target.iterdir() if f.is_file()]

    upserted = 0
    failed = 0
    errors: list[str] = []

    for f in files:
        for i, data in enumerate(_parse_file(f)):
            label = f"{f.name}[{i}]" if i else f.name
            try:
                import_host(session, data)
                upserted += 1
            except Exception as e:
                failed += 1
                errors.append(f"{label}: {e}")

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
