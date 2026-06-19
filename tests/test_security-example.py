from cmdb.domain.models import Host
from cmdb.domain.services.security import host_posture, posture_summary


def _host(name: str, *, apparmor=None, selinux=None, fips=None) -> Host:
    return Host(
        machine_id=name,
        hostname=name,
        apparmor_status=apparmor,
        selinux_status=selinux,
        fips=fips,
    )


def test_apparmor_enabled_is_hardened():
    p = host_posture(_host("a", apparmor="enabled", selinux="disabled"))
    assert p.hardened is True
    assert p.mac == "AppArmor"
    assert p.issues == []


def test_selinux_enforcing_is_hardened():
    p = host_posture(_host("b", apparmor=None, selinux="enforcing"))
    assert p.hardened is True
    assert p.mac == "SELinux"
    assert p.issues == []


def test_selinux_enabled_is_hardened():
    p = host_posture(_host("b2", selinux="enabled"))
    assert p.hardened is True
    assert p.mac == "SELinux"


def test_apparmor_takes_precedence_over_selinux():
    p = host_posture(_host("c", apparmor="enabled", selinux="enforcing"))
    assert p.mac == "AppArmor"


def test_selinux_permissive_is_not_hardened():
    p = host_posture(_host("d", apparmor="disabled", selinux="permissive"))
    assert p.hardened is False
    assert p.mac is None
    assert any("permissive" in i.lower() for i in p.issues)


def test_no_mac_active_is_exposed():
    p = host_posture(_host("e", apparmor="disabled", selinux="disabled"))
    assert p.hardened is False
    assert p.mac is None
    assert p.issues  # has at least one issue


def test_missing_values_are_exposed():
    p = host_posture(_host("f"))
    assert p.hardened is False
    assert p.mac is None


def test_fips_is_informational_not_a_flag():
    # FIPS off must not, on its own, make a hardened host exposed
    p = host_posture(_host("g", apparmor="enabled", fips=False))
    assert p.hardened is True
    assert p.fips is False
    assert p.issues == []


def test_fips_value_is_carried():
    assert host_posture(_host("h", apparmor="enabled", fips=True)).fips is True


def test_posture_summary_counts():
    hosts = [
        _host("h1", apparmor="enabled"),
        _host("h2", selinux="enforcing", fips=True),
        _host("h3", apparmor="disabled", selinux="disabled"),
        _host("h4", selinux="permissive"),
    ]
    s = posture_summary(hosts)
    assert s["total"] == 4
    assert s["hardened"] == 2
    assert s["exposed"] == 2
    assert s["fips_on"] == 1
    exposed_names = {h.hostname for h in s["exposed_hosts"]}
    assert exposed_names == {"h3", "h4"}


def test_posture_summary_empty():
    s = posture_summary([])
    assert s == {
        "total": 0,
        "hardened": 0,
        "exposed": 0,
        "fips_on": 0,
        "exposed_hosts": [],
    }
