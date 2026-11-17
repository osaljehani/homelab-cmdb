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


def test_import_from_path_malformed_json_does_not_raise(db, tmp_path):
    (tmp_path / "broken.json").write_text('{"ansible_facts": {"ansible_hostn')
    log = import_from_path(db, str(tmp_path), ImportSource.CLI)
    assert log.hosts_upserted == 0
    assert log.hosts_failed == 1
    assert "JSON parse error" in log.notes


def test_import_from_path_binary_file_does_not_raise(db, tmp_path):
    (tmp_path / "facts.tar.gz").write_bytes(b"\x1f\x8b\x08\x00\xff\xfe\xfd\xfc")
    log = import_from_path(db, str(tmp_path), ImportSource.CLI)
    assert log.hosts_upserted == 0
    assert log.hosts_failed == 1
    assert "JSON parse error" in log.notes


def test_import_from_path_good_file_survives_bad_sibling(db, tmp_path, host_facts):
    import json

    (tmp_path / "broken.json").write_text("not json at all")
    (tmp_path / "testhost").write_text(json.dumps(host_facts))
    log = import_from_path(db, str(tmp_path), ImportSource.CLI)
    assert log.hosts_upserted == 1
    assert log.hosts_failed == 1


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


# --- rebuilt host: new machine-id, same hostname + primary MAC ---------------


def _rebuilt(facts: dict, machine_id: str = "99887766554433229988776655443322") -> dict:
    import copy

    data = copy.deepcopy(facts)
    data["ansible_facts"]["ansible_machine_id"] = machine_id
    return data


def test_rebuilt_host_rekeys_existing_record(db, host_facts):
    """An OS reinstall regenerates /etc/machine-id; the box is still the same
    host, so the existing record is re-keyed instead of duplicated."""
    from cmdb.domain.models import Host

    original = import_host(db, host_facts)
    assert original.primary_mac  # the fixture must carry a MAC for this to mean anything
    rebuilt = import_host(db, _rebuilt(host_facts))

    assert rebuilt.id == original.id
    assert rebuilt.machine_id == "99887766554433229988776655443322"
    assert db.query(Host).count() == 1


def test_rebuilt_host_rekey_is_recorded_in_history(db, host_facts):
    from cmdb.domain.services.history import host_history

    import_host(db, host_facts)
    host = import_host(db, _rebuilt(host_facts))

    changed = [c[0] for entry in host_history(db, host) for c in entry["changes"]]
    assert "machine_id" in changed


def test_new_machine_id_with_different_mac_is_a_new_host(db, host_facts):
    from cmdb.domain.models import Host

    import_host(db, host_facts)
    other = _rebuilt(host_facts)
    other["ansible_facts"]["ansible_default_ipv4"]["macaddress"] = "02:00:5e:00:53:99"
    import_host(db, other)

    assert db.query(Host).count() == 2


def test_new_machine_id_with_different_hostname_is_a_new_host(db, host_facts):
    from cmdb.domain.models import Host

    import_host(db, host_facts)
    other = _rebuilt(host_facts)
    other["ansible_facts"]["ansible_hostname"] = "otherhost"
    import_host(db, other)

    assert db.query(Host).count() == 2


def test_new_machine_id_without_mac_is_a_new_host(db, host_facts):
    """No MAC on either side is not a match — hostname alone never merges."""
    from cmdb.domain.models import Host

    bare = _rebuilt(host_facts, "aabbccdd11223344aabbccdd11223344")
    bare["ansible_facts"]["ansible_default_ipv4"].pop("macaddress", None)
    import_host(db, bare)
    import_host(db, _rebuilt(bare))

    assert db.query(Host).count() == 2


def test_ambiguous_rebuild_match_is_a_new_host(db, host_facts):
    """Two existing records already share hostname + MAC: don't guess."""
    from cmdb.domain.models import Host

    first = import_host(db, host_facts)
    second = import_host(db, _rebuilt(host_facts, "1111111111111111aaaaaaaaaaaaaaaa"))
    # force the pre-existing duplicate state (what a pre-fix DB can contain)
    assert first.id == second.id
    dup = Host(
        machine_id="2222222222222222bbbbbbbbbbbbbbbb",
        hostname=first.hostname,
        primary_mac=first.primary_mac,
    )
    db.add(dup)
    db.flush()

    import_host(db, _rebuilt(host_facts, "3333333333333333cccccccccccccccc"))
    assert db.query(Host).count() == 3
