import pytest
from cmdb.domain.services.ansible import import_host, import_from_path
from cmdb.domain.models import ImportSource


def test_import_maps_hostname(db, host_facts):
    host = import_host(db, host_facts)
    assert host.hostname == "testhost"


def test_import_maps_machine_id(db, host_facts):
    host = import_host(db, host_facts)
    assert host.machine_id == "aabbccdd11223344aabbccdd11223344"


def test_import_maps_cpu_model(db, host_facts):
    host = import_host(db, host_facts)
    assert "Xeon" in host.cpu_model


def test_import_maps_primary_ipv4(db, host_facts):
    host = import_host(db, host_facts)
    assert host.primary_ipv4 == "192.168.1.10"


def test_import_maps_apparmor(db, host_facts):
    host = import_host(db, host_facts)
    assert host.apparmor_status == "enabled"


def test_import_maps_selinux(db, host_facts):
    host = import_host(db, host_facts)
    assert host.selinux_status == "disabled"


def test_import_preserves_raw_facts(db, host_facts):
    host = import_host(db, host_facts)
    assert host.raw_facts is not None
    assert "ansible_devices" in host.raw_facts


def test_import_upserts_on_machine_id(db, host_facts, host_facts_alt):
    host1 = import_host(db, host_facts)
    host2 = import_host(db, host_facts_alt)
    assert host1.id == host2.id
    assert host2.hostname == "testhost-renamed"


def test_import_missing_machine_id_raises(db):
    with pytest.raises(ValueError, match="ansible_machine_id"):
        import_host(db, {"ansible_facts": {"ansible_hostname": "orphan"}})


def test_import_from_path_single_file(db, tmp_path, host_facts):
    import json
    f = tmp_path / "testhost"
    f.write_text(json.dumps(host_facts))
    log = import_from_path(db, str(tmp_path), ImportSource.CLI)
    assert log.hosts_upserted == 1
    assert log.hosts_failed == 0


def test_import_multi_host_stdout_file(db, tmp_path, host_facts, host_facts_alt):
    import json
    block1 = json.dumps(host_facts)
    block2_facts = host_facts_alt.copy()
    block2_facts["ansible_facts"] = dict(host_facts_alt["ansible_facts"])
    block2_facts["ansible_facts"]["ansible_machine_id"] = "deadbeef" * 4
    block2_facts["ansible_facts"]["ansible_hostname"] = "node2"
    block2 = json.dumps(block2_facts)
    combined = f"host1 | SUCCESS => {block1}\nhost2 | SUCCESS => {block2}\n"
    f = tmp_path / "homelab.json"
    f.write_text(combined)
    log = import_from_path(db, str(f), ImportSource.CLI)
    assert log.hosts_upserted == 2
    assert log.hosts_failed == 0


def test_import_from_path_idempotent(db, tmp_path, host_facts):
    import json
    f = tmp_path / "testhost"
    f.write_text(json.dumps(host_facts))
    import_from_path(db, str(tmp_path), ImportSource.CLI)
    log2 = import_from_path(db, str(tmp_path), ImportSource.CLI)
    assert log2.hosts_upserted == 1
    from cmdb.domain.models import Host
    assert db.query(Host).count() == 1
