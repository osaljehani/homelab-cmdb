"""Tests for the MCP tool layer.

Tools wrap domain services and serialize to Pydantic models. We call the
(undecorated) tool functions directly — FastMCP's @mcp.tool() returns the
original function — with get_session patched to the in-memory test session.
"""

from contextlib import contextmanager

import pytest

from cmdb.domain.services.ansible import import_host
from cmdb.mcp import server


@pytest.fixture
def mcp_db(db, host_facts, monkeypatch):
    """In-memory DB with one host, wired into the MCP server's get_session."""
    import_host(db, host_facts)

    @contextmanager
    def _fake_get_session():
        yield db

    monkeypatch.setattr(server, "get_session", _fake_get_session)
    return db


# --- Hosts -----------------------------------------------------------------


def test_list_hosts(mcp_db):
    hosts = server.list_hosts()
    assert len(hosts) == 1
    assert hosts[0].hostname == "testhost"
    assert hosts[0].primary_ipv4 == "192.168.1.10"


def test_list_hosts_filter_by_tag(mcp_db):
    server.add_tag("testhost", "proxmox")
    assert len(server.list_hosts(tag="proxmox")) == 1
    assert len(server.list_hosts(tag="missing")) == 0


def test_get_host_detail(mcp_db):
    host = server.get_host("testhost")
    assert host.hostname == "testhost"
    assert host.os_family == "Debian"
    assert host.containers == []
    assert host.listening_ports == []


def test_get_host_not_found_raises(mcp_db):
    with pytest.raises(ValueError, match="not found"):
        server.get_host("ghost")


def test_add_and_remove_tag(mcp_db):
    host = server.add_tag("testhost", "PROXMOX")
    assert "proxmox" in host.tags  # lowercased by the service
    host = server.remove_tag("testhost", "proxmox")
    assert "proxmox" not in host.tags


def test_delete_host(mcp_db):
    assert server.delete_host("testhost") is True
    assert server.list_hosts() == []
    assert server.delete_host("testhost") is False


# --- Security --------------------------------------------------------------


def test_host_posture(mcp_db):
    # testhost has apparmor enabled in the sample facts.
    p = server.host_posture("testhost")
    assert p.hardened is True
    assert p.mac == "AppArmor"


def test_posture_summary(mcp_db):
    s = server.posture_summary()
    assert s.total == 1
    assert s.hardened == 1
    assert s.exposed == 0
    assert s.exposed_hostnames == []


# --- History ---------------------------------------------------------------


def test_host_history_initial(mcp_db):
    history = server.host_history("testhost")
    assert len(history) == 1
    assert history[0].initial is True


# --- Kubernetes ------------------------------------------------------------


def test_k8s_cluster_and_node_lifecycle(mcp_db):
    cluster = server.add_cluster("homelab", "primary cluster")
    assert cluster.name == "homelab"
    assert cluster.node_count == 0

    node = server.add_node("testhost", "homelab", "worker")
    assert node.hostname == "testhost"
    assert node.role == "worker"

    clusters = server.list_clusters()
    assert clusters[0].node_count == 1

    nodes = server.list_nodes("homelab")
    assert [n.hostname for n in nodes] == ["testhost"]

    assert server.remove_node("testhost", "homelab") is True
    assert server.list_nodes("homelab") == []
    assert server.delete_cluster("homelab") is True


def test_add_node_invalid_role_raises(mcp_db):
    server.add_cluster("homelab")
    with pytest.raises(ValueError, match="Invalid role"):
        server.add_node("testhost", "homelab", "captain")


# --- Generation ------------------------------------------------------------


def test_generate_inventory_yaml(mcp_db):
    out = server.generate_inventory_yaml()
    assert "testhost" in out


def test_generate_ssh_config(mcp_db):
    out = server.generate_ssh_config()
    assert "Host testhost" in out
