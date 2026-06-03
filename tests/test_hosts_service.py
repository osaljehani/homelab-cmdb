import pytest
from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.hosts import (
    list_hosts, get_host, add_tag, remove_tag, delete_host
)


@pytest.fixture
def populated_db(db, host_facts):
    import_host(db, host_facts)
    return db


def test_list_hosts_returns_all(populated_db):
    hosts = list_hosts(populated_db)
    assert len(hosts) == 1
    assert hosts[0].hostname == "testhost"


def test_list_hosts_filter_by_tag(populated_db):
    add_tag(populated_db, "testhost", "proxmox")
    result = list_hosts(populated_db, tag="proxmox")
    assert len(result) == 1
    result_none = list_hosts(populated_db, tag="missing")
    assert len(result_none) == 0


def test_list_hosts_filter_by_os(populated_db):
    result = list_hosts(populated_db, os_family="Debian")
    assert len(result) == 1
    result_none = list_hosts(populated_db, os_family="RedHat")
    assert len(result_none) == 0


def test_get_host_found(populated_db):
    host = get_host(populated_db, "testhost")
    assert host is not None
    assert host.primary_ipv4 == "192.168.1.10"


def test_get_host_not_found(populated_db):
    assert get_host(populated_db, "nonexistent") is None


def test_add_tag(populated_db):
    host = add_tag(populated_db, "testhost", "proxmox")
    assert any(t.name == "proxmox" for t in host.tags)


def test_add_tag_lowercases(populated_db):
    host = add_tag(populated_db, "testhost", "PROXMOX")
    assert any(t.name == "proxmox" for t in host.tags)


def test_add_tag_idempotent(populated_db):
    add_tag(populated_db, "testhost", "proxmox")
    add_tag(populated_db, "testhost", "proxmox")
    host = get_host(populated_db, "testhost")
    assert sum(1 for t in host.tags if t.name == "proxmox") == 1


def test_add_tag_unknown_host_raises(populated_db):
    with pytest.raises(ValueError, match="not found"):
        add_tag(populated_db, "ghost", "proxmox")


def test_remove_tag(populated_db):
    add_tag(populated_db, "testhost", "proxmox")
    host = remove_tag(populated_db, "testhost", "proxmox")
    assert not any(t.name == "proxmox" for t in host.tags)


def test_delete_host(populated_db):
    assert delete_host(populated_db, "testhost") is True
    assert get_host(populated_db, "testhost") is None


def test_delete_host_not_found(populated_db):
    assert delete_host(populated_db, "ghost") is False
