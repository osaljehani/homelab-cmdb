import yaml
import pytest
from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.hosts import add_tag
from cmdb.domain.services.generate import (
    generate_inventory_yaml, generate_inventory_ini, generate_ssh_config
)


@pytest.fixture
def populated_db(db, host_facts):
    import_host(db, host_facts)
    return db


def test_inventory_yaml_contains_hostname(populated_db):
    out = generate_inventory_yaml(populated_db)
    data = yaml.safe_load(out)
    assert "testhost" in data["all"]["hosts"]


def test_inventory_yaml_contains_ansible_host(populated_db):
    out = generate_inventory_yaml(populated_db)
    data = yaml.safe_load(out)
    assert data["all"]["hosts"]["testhost"]["ansible_host"] == "192.168.1.10"


def test_inventory_yaml_tag_filter(populated_db):
    add_tag(populated_db, "testhost", "proxmox")
    out_all = generate_inventory_yaml(populated_db)
    out_tagged = generate_inventory_yaml(populated_db, tag="proxmox")
    out_none = generate_inventory_yaml(populated_db, tag="missing")
    assert "testhost" in yaml.safe_load(out_all)["all"]["hosts"]
    assert "testhost" in yaml.safe_load(out_tagged)["all"]["hosts"]
    assert not yaml.safe_load(out_none)["all"]["hosts"]


def test_inventory_ini_contains_hostname(populated_db):
    out = generate_inventory_ini(populated_db)
    assert "testhost" in out
    assert "ansible_host=192.168.1.10" in out


def test_inventory_ini_starts_with_all(populated_db):
    out = generate_inventory_ini(populated_db)
    assert out.startswith("[all]")


def test_ssh_config_contains_host_block(populated_db):
    out = generate_ssh_config(populated_db)
    assert "Host testhost" in out


def test_ssh_config_contains_hostname_line(populated_db):
    out = generate_ssh_config(populated_db)
    assert "HostName 192.168.1.10" in out or "HostName testhost.local" in out


def test_ssh_config_tag_filter(populated_db):
    add_tag(populated_db, "testhost", "proxmox")
    out_tagged = generate_ssh_config(populated_db, tag="proxmox")
    out_none = generate_ssh_config(populated_db, tag="missing")
    assert "Host testhost" in out_tagged
    assert out_none.strip() == ""
