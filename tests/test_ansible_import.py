import pytest
from cmdb.domain.services.ansible import import_host, import_from_path
from cmdb.domain.models import ImportSource


def test_import_maps_hostname(db, blade14_facts):
    host = import_host(db, blade14_facts)
    assert host.hostname == "blade14"


def test_import_maps_machine_id(db, blade14_facts):
    host = import_host(db, blade14_facts)
    assert host.machine_id == "aabbccdd11223344aabbccdd11223344"


def test_import_maps_cpu_model(db, blade14_facts):
    host = import_host(db, blade14_facts)
    assert "Xeon" in host.cpu_model


def test_import_maps_primary_ipv4(db, blade14_facts):
    host = import_host(db, blade14_facts)
    assert host.primary_ipv4 == "192.168.0.14"


def test_import_maps_apparmor(db, blade14_facts):
    host = import_host(db, blade14_facts)
    assert host.apparmor_status == "enabled"


def test_import_maps_selinux(db, blade14_facts):
    host = import_host(db, blade14_facts)
    assert host.selinux_status == "disabled"


def test_import_preserves_raw_facts(db, blade14_facts):
    host = import_host(db, blade14_facts)
    assert host.raw_facts is not None
    assert "ansible_devices" in host.raw_facts


def test_import_upserts_on_machine_id(db, blade14_facts, blade14_facts_alt):
    host1 = import_host(db, blade14_facts)
    host2 = import_host(db, blade14_facts_alt)
    assert host1.id == host2.id
    assert host2.hostname == "blade14-renamed"


def test_import_missing_machine_id_raises(db):
    with pytest.raises(ValueError, match="ansible_machine_id"):
        import_host(db, {"ansible_facts": {"ansible_hostname": "orphan"}})


def test_import_from_path_single_file(db, tmp_path, blade14_facts):
    import json
    f = tmp_path / "blade14"
    f.write_text(json.dumps(blade14_facts))
    log = import_from_path(db, str(tmp_path), ImportSource.CLI)
    assert log.hosts_upserted == 1
    assert log.hosts_failed == 0


def test_import_from_path_idempotent(db, tmp_path, blade14_facts):
    import json
    f = tmp_path / "blade14"
    f.write_text(json.dumps(blade14_facts))
    import_from_path(db, str(tmp_path), ImportSource.CLI)
    log2 = import_from_path(db, str(tmp_path), ImportSource.CLI)
    assert log2.hosts_upserted == 1
    from cmdb.domain.models import Host
    assert db.query(Host).count() == 1
