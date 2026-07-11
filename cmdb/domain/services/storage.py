"""Storage facts parsed on read from Host.raw_facts (ansible_devices/ansible_mounts).

No table or import hook: raw_facts is refreshed on every facts import, so a
pure read-side parse is always as current as the inventory itself.
"""

from sqlalchemy.orm import Session

from cmdb.domain.models import Host

# Pseudo/ephemeral filesystems that say nothing about disk capacity.
_PSEUDO_FSTYPES = {"squashfs", "tmpfs", "devtmpfs", "overlay", "iso9660"}

# Virtual block devices that aren't disks.
_SKIP_DEVICE_PREFIXES = ("loop", "ram")


def host_storage(host: Host) -> dict:
    """Devices and real mounts for one host, with used-space percentages.

    Returns ``{"devices": [{"name", "model", "size", "rotational"}],
    "mounts": [{"mount", "device", "fstype", "size_total", "size_available",
    "used_pct"}]}`` — empty lists when facts are missing.
    """
    facts = host.raw_facts or {}

    devices = []
    for name in sorted(facts.get("ansible_devices") or {}):
        if name.startswith(_SKIP_DEVICE_PREFIXES):
            continue
        d = facts["ansible_devices"][name] or {}
        devices.append(
            {
                "name": name,
                "model": d.get("model"),
                "size": d.get("size"),
                "rotational": d.get("rotational") == "1",
            }
        )

    mounts = []
    for m in facts.get("ansible_mounts") or []:
        total = m.get("size_total")
        if not total or (m.get("fstype") or "").lower() in _PSEUDO_FSTYPES:
            continue
        available = m.get("size_available") or 0
        mounts.append(
            {
                "mount": m.get("mount"),
                "device": m.get("device"),
                "fstype": m.get("fstype"),
                "size_total": total,
                "size_available": available,
                "used_pct": round((total - available) * 100 / total),
            }
        )
    mounts.sort(key=lambda m: m["mount"] or "")

    return {"devices": devices, "mounts": mounts}


def fleet_storage(session: Session, warn_pct: int) -> dict:
    """Fleet rollup: per-host totals plus low-free-space warnings, worst first."""
    hosts = []
    warnings = []
    for host in session.query(Host).order_by(Host.hostname).all():
        st = host_storage(host)
        if not st["mounts"]:
            continue
        hosts.append(
            {
                "hostname": host.hostname,
                "mounts": len(st["mounts"]),
                "size_total": sum(m["size_total"] for m in st["mounts"]),
                "size_available": sum(m["size_available"] for m in st["mounts"]),
            }
        )
        for m in st["mounts"]:
            if m["used_pct"] >= warn_pct:
                warnings.append({"hostname": host.hostname, **m})
    warnings.sort(key=lambda w: -w["used_pct"])
    return {"hosts": hosts, "warnings": warnings}
