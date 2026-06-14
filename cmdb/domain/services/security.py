"""Security posture evaluation for hosts.

Surfaces the AppArmor / SELinux / FIPS facts already captured on each host into
a simple posture judgement. AppArmor and SELinux are mutually exclusive by
distribution, so a host is considered *hardened* when either MAC is active and
*exposed* when neither is. FIPS is informational only (off is normal in a
homelab) and never drives the exposed flag.
"""

from dataclasses import dataclass, field

from cmdb.domain.models import Host


def _is(value: str | None, *ok: str) -> bool:
    return bool(value) and value.strip().lower() in ok


@dataclass
class HostPosture:
    hardened: bool  # at least one MAC (AppArmor/SELinux) active
    mac: str | None  # "AppArmor" | "SELinux" | None   the active MAC, if any
    fips: bool | None  # informational, not a flag
    issues: list[str] = field(default_factory=list)


def host_posture(host: Host) -> HostPosture:
    """Evaluate a single host's security posture."""
    if _is(host.apparmor_status, "enabled"):
        return HostPosture(hardened=True, mac="AppArmor", fips=host.fips)
    if _is(host.selinux_status, "enforcing", "enabled"):
        return HostPosture(hardened=True, mac="SELinux", fips=host.fips)

    issues: list[str] = []
    if _is(host.selinux_status, "permissive"):
        issues.append("SELinux permissive")
    else:
        issues.append("No MAC active (AppArmor/SELinux inactive)")
    return HostPosture(hardened=False, mac=None, fips=host.fips, issues=issues)


def posture_summary(hosts: list[Host]) -> dict:
    """Aggregate posture across hosts for the dashboard panel."""
    hardened = 0
    fips_on = 0
    exposed_hosts: list[Host] = []
    for host in hosts:
        p = host_posture(host)
        if p.hardened:
            hardened += 1
        else:
            exposed_hosts.append(host)
        if p.fips:
            fips_on += 1
    return {
        "total": len(hosts),
        "hardened": hardened,
        "exposed": len(exposed_hosts),
        "fips_on": fips_on,
        "exposed_hosts": exposed_hosts,
    }
