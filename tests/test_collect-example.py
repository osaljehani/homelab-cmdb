import json
import subprocess
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from cmdb.domain.models import Container, Host, ImportSource, K8sCluster
from cmdb.domain.services import collect as collect_mod
from cmdb.domain.services.ansible import import_host
from cmdb.domain.services.collect import (
    CollectError,
    collect_docker,
    collect_facts,
    collect_k8s,
    resolve_inventory,
)

INVENTORY = "test-inventory.ini"


def _tree_dir(cmd: list[str]) -> Path:
    return Path(cmd[cmd.index("--tree") + 1])


def _fake_run(writer, returncode=0, stderr=""):
    """Build a subprocess.run replacement that writes canned --tree output."""

    def run(cmd, **kwargs):
        writer(_tree_dir(cmd), cmd)
        return subprocess.CompletedProcess(cmd, returncode, stdout="", stderr=stderr)

    return run


# --- collect_facts ---


def test_collect_facts_imports_host(db: Session, host_facts: dict, monkeypatch):
    def writer(tree: Path, cmd):
        (tree / "testhost").write_text(json.dumps(host_facts))

    monkeypatch.setattr(collect_mod.subprocess, "run", _fake_run(writer))

    log = collect_facts(db, INVENTORY)

    assert log.source == ImportSource.COLLECT
    assert log.hosts_upserted == 1
    assert log.hosts_failed == 0
    assert INVENTORY in log.filename
    host = db.query(Host).filter_by(hostname="testhost").one()
    assert host.last_seen is not None


def test_collect_facts_records_unreachable_host(db: Session, monkeypatch):
    def writer(tree: Path, cmd):
        # ansible --tree writes a result file even for unreachable hosts; it has no
        # machine_id, so the existing importer records it as a failure.
        (tree / "deadhost").write_text(
            json.dumps({"unreachable": True, "msg": "ssh: connect timed out"})
        )

    monkeypatch.setattr(collect_mod.subprocess, "run", _fake_run(writer))

    log = collect_facts(db, INVENTORY)

    assert log.hosts_upserted == 0
    assert log.hosts_failed == 1
    assert "deadhost" in log.notes


# --- collect_docker ---


@pytest.fixture
def host_db(db: Session, host_facts: dict) -> Session:
    import_host(db, host_facts)
    db.flush()
    return db


def test_collect_docker_imports_containers(host_db: Session, monkeypatch):
    stdout = "\n".join(
        json.dumps(c)
        for c in [
            {"Names": "nginx", "Image": "nginx:latest", "State": "running",
             "Status": "Up 3 days", "Ports": "0.0.0.0:80->80/tcp",
             "Labels": "com.docker.compose.project=web"},
            {"Names": "redis", "Image": "redis:7", "State": "exited",
             "Status": "Exited (0)"},
        ]
    )

    def writer(tree: Path, cmd):
        (tree / "testhost").write_text(json.dumps({"rc": 0, "stdout": stdout}))

    monkeypatch.setattr(collect_mod.subprocess, "run", _fake_run(writer))

    log = collect_docker(host_db, INVENTORY)

    assert log.source == ImportSource.COLLECT
    assert log.containers_upserted == 2
    names = {c.name for c in host_db.query(Container).all()}
    assert names == {"nginx", "redis"}


def test_collect_docker_skips_failed_host_without_wiping(host_db: Session, monkeypatch):
    # Pre-existing container that must survive a failed collection.
    host = host_db.query(Host).one()
    host_db.add(Container(host_id=host.id, name="keepme", image="busybox"))
    host_db.flush()

    def writer(tree: Path, cmd):
        (tree / "testhost").write_text(
            json.dumps({"rc": 127, "stderr": "docker: command not found"})
        )

    monkeypatch.setattr(
        collect_mod.subprocess, "run", _fake_run(writer, returncode=2)
    )

    log = collect_docker(host_db, INVENTORY)

    assert log.containers_upserted == 0
    assert "testhost" in log.notes
    # Replace-on-import must NOT have run for the failed host.
    assert {c.name for c in host_db.query(Container).all()} == {"keepme"}


def test_collect_docker_skips_host_without_docker_silently(host_db: Session, monkeypatch):
    # A pure k3s node has no docker binary. The probe reports the no-docker sentinel
    # (exit 0). That is an expected condition, not an error: it must not pollute the
    # notes and must not wipe the host's existing containers.
    host = host_db.query(Host).one()
    host_db.add(Container(host_id=host.id, name="keepme", image="busybox"))
    host_db.flush()

    def writer(tree: Path, cmd):
        (tree / "testhost").write_text(
            json.dumps({"rc": 0, "stdout": "status=no-docker"})
        )

    monkeypatch.setattr(collect_mod.subprocess, "run", _fake_run(writer))

    log = collect_docker(host_db, INVENTORY)

    assert log.containers_upserted == 0
    assert not log.notes  # a host without docker installed is not an error
    assert {c.name for c in host_db.query(Container).all()} == {"keepme"}


def test_docker_ps_cmd_guards_for_missing_docker():
    # The probe must short-circuit on hosts without docker rather than letting
    # `docker ps` fail with rc 127 and surface a confusing generic error.
    assert "status=no-docker" in collect_mod._DOCKER_PS_CMD


def test_docker_ps_cmd_has_no_jinja_braces():
    # Ansible runs the shell module's args through Jinja2; a '{{json .}}' format
    # string is parsed as a Jinja expression and fails before docker ever runs.
    assert "{{" not in collect_mod._DOCKER_PS_CMD
    assert "}}" not in collect_mod._DOCKER_PS_CMD


def test_strip_ansible_warnings_removes_warning_lines():
    text = (
        "[WARNING]: Host 'edge-node' is using the discovered Python interpreter at x.\n"
        "docker: command not found\n"
        "[DEPRECATION WARNING]: something deprecated."
    )
    cleaned = collect_mod._strip_ansible_warnings(text)
    assert "[WARNING]" not in cleaned
    assert "[DEPRECATION WARNING]" not in cleaned
    assert "docker: command not found" in cleaned


def test_collect_docker_filters_ansible_warning_noise(host_db: Session, monkeypatch):
    # Interpreter-discovery [WARNING] lines on stderr are noise; the real per-host
    # error must remain and the warning must not pollute the notes.
    warning = (
        "[WARNING]: Host 'testhost' is using the discovered Python interpreter at "
        "'/usr/bin/python3.11', but future installation could change it."
    )

    def writer(tree: Path, cmd):
        (tree / "testhost").write_text(
            json.dumps({"rc": 127, "stderr": "docker: command not found"})
        )

    monkeypatch.setattr(
        collect_mod.subprocess, "run",
        _fake_run(writer, returncode=2, stderr=warning),
    )

    log = collect_docker(host_db, INVENTORY)

    assert "[WARNING]" not in (log.notes or "")
    assert "testhost" in log.notes


def test_collect_docker_surfaces_failure_reported_on_stdout(host_db: Session, monkeypatch):
    # Ansible can fail before writing any --tree file (e.g. arg templating error) and
    # emit the failure on stdout with an empty stderr. That must not be swallowed.
    def run(cmd, **kwargs):
        # no tree files written
        return subprocess.CompletedProcess(
            cmd, 4,
            stdout="Test-Node-1 | FAILED | rc=-1 >>\nTask failed: Syntax error in template",
            stderr="",
        )

    monkeypatch.setattr(collect_mod.subprocess, "run", run)

    log = collect_docker(host_db, INVENTORY)

    assert log.containers_upserted == 0
    assert log.notes, "a non-zero ansible run with output must be recorded in notes"
    assert "FAILED" in log.notes or "Task failed" in log.notes


# --- collect_k8s ---


def _k8s_blob(context: str, node_items: list, ns_items: list) -> str:
    """Build the marker-delimited stdout the remote probe emits for a control-plane."""
    return "\n".join([
        "__CMDB_K8S__",
        "status=ok",
        f"context={context}",
        "__NODES__",
        json.dumps({"items": node_items}),
        "__NAMESPACES__",
        json.dumps({"items": ns_items}),
    ])


def _node(name: str, role: str | None = None) -> dict:
    labels = {}
    if role == "control-plane":
        labels = {"node-role.kubernetes.io/control-plane": ""}
    elif role == "etcd":
        labels = {"node-role.kubernetes.io/etcd": ""}
    return {"metadata": {"name": name, "labels": labels}}


def test_collect_k8s_imports_cluster(host_db: Session, monkeypatch):
    blob = _k8s_blob(
        "local",
        [_node("testhost", "control-plane")],
        [{"metadata": {"name": "default"}}, {"metadata": {"name": "kube-system"}}],
    )

    def writer(tree: Path, cmd):
        (tree / "testhost").write_text(json.dumps({"rc": 0, "stdout": blob}))

    monkeypatch.setattr(collect_mod.subprocess, "run", _fake_run(writer))

    log = collect_k8s(host_db, INVENTORY)

    assert log.source == ImportSource.COLLECT
    assert log.k8s_clusters_upserted == 1
    assert log.k8s_nodes_upserted == 1
    assert log.k8s_namespaces_upserted == 2
    assert host_db.query(K8sCluster).one().name == "local"


def test_collect_k8s_skips_non_control_plane_silently(host_db: Session, monkeypatch):
    def writer(tree: Path, cmd):
        # worker / non-k8s host: the probe finds no usable kubectl
        (tree / "testhost").write_text(json.dumps({"rc": 0, "stdout": "status=no-k8s"}))

    monkeypatch.setattr(collect_mod.subprocess, "run", _fake_run(writer))

    log = collect_k8s(host_db, INVENTORY)

    assert log.k8s_clusters_upserted == 0
    assert not log.notes  # a host that simply isn't a control-plane is not an error
    assert host_db.query(K8sCluster).count() == 0


def test_collect_k8s_generic_context_falls_back_to_hostname(host_db: Session, monkeypatch):
    # k3s reports the generic context 'default'; the cluster is named after the host.
    blob = _k8s_blob("default", [_node("testhost", "control-plane")],
                     [{"metadata": {"name": "default"}}])

    def writer(tree: Path, cmd):
        (tree / "testhost").write_text(json.dumps({"rc": 0, "stdout": blob}))

    monkeypatch.setattr(collect_mod.subprocess, "run", _fake_run(writer))

    collect_k8s(host_db, INVENTORY)

    assert host_db.query(K8sCluster).one().name == "testhost"


def test_collect_k8s_unknown_node_recorded_in_notes(host_db: Session, monkeypatch):
    # A control-plane enumerates a worker that isn't in the CMDB yet surfaced, not fatal.
    blob = _k8s_blob(
        "local",
        [_node("testhost", "control-plane"), _node("ghost", None)],
        [{"metadata": {"name": "default"}}],
    )

    def writer(tree: Path, cmd):
        (tree / "testhost").write_text(json.dumps({"rc": 0, "stdout": blob}))

    monkeypatch.setattr(collect_mod.subprocess, "run", _fake_run(writer))

    log = collect_k8s(host_db, INVENTORY)

    assert log.k8s_nodes_upserted == 1  # only the known host linked
    assert "ghost" in log.notes


def test_collect_k8s_records_unreachable_host(host_db: Session, monkeypatch):
    def writer(tree: Path, cmd):
        (tree / "deadhost").write_text(
            json.dumps({"unreachable": True, "msg": "ssh: connect timed out"})
        )

    monkeypatch.setattr(collect_mod.subprocess, "run", _fake_run(writer))

    log = collect_k8s(host_db, INVENTORY)

    assert log.k8s_clusters_upserted == 0
    assert "deadhost" in log.notes


def test_k8s_probe_cmd_has_no_jinja_braces():
    # Ansible runs the shell module's args through Jinja2; '{{' would be parsed as an
    # expression and fail before kubectl ever runs.
    assert "{{" not in collect_mod._K8S_PROBE_CMD
    assert "}}" not in collect_mod._K8S_PROBE_CMD


# --- collect_ports ---


def test_collect_ports_imports_listeners(host_db: Session, monkeypatch):
    from cmdb.domain.models import ListeningPort
    stdout = (
        "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:* users:((\"sshd\",pid=1,fd=3))\n"
        "udp UNCONN 0 0   0.0.0.0:53 0.0.0.0:* users:((\"resolved\",pid=2,fd=4))\n"
    )

    def writer(tree: Path, cmd):
        (tree / "testhost").write_text(json.dumps({"rc": 0, "stdout": stdout}))

    monkeypatch.setattr(collect_mod.subprocess, "run", _fake_run(writer))

    log = collect_mod.collect_ports(host_db, INVENTORY)

    assert log.listening_ports_upserted == 2
    assert {p.port for p in host_db.query(ListeningPort).all()} == {22, 53}


def test_collect_ports_skips_failed_host_without_wiping(host_db: Session, monkeypatch):
    from cmdb.domain.models import Host, ListeningPort
    host = host_db.query(Host).one()
    host_db.add(ListeningPort(host_id=host.id, proto="tcp", address="0.0.0.0",
                              port=22, process="sshd"))
    host_db.flush()

    def writer(tree: Path, cmd):
        (tree / "testhost").write_text(
            json.dumps({"rc": 127, "stderr": "ss: command not found"})
        )

    monkeypatch.setattr(collect_mod.subprocess, "run",
                        _fake_run(writer, returncode=2))

    log = collect_mod.collect_ports(host_db, INVENTORY)

    assert log.listening_ports_upserted == 0
    assert "testhost" in log.notes
    assert host_db.query(ListeningPort).count() == 1  # preserved


def test_ports_cmd_has_no_jinja_braces():
    assert "{{" not in collect_mod._PORTS_CMD
    assert "}}" not in collect_mod._PORTS_CMD


# --- collect_tailscale ---

def _ts_blob(status: str, serve: str) -> str:
    return "\n".join(["__CMDB_TS__", "__STATUS__", status, "__SERVE__", serve])


def test_collect_tailscale_updates_host(host_db: Session, monkeypatch):
    from cmdb.domain.models import Host, TailscaleService
    status = json.dumps({"Self": {
        "TailscaleIPs": ["100.64.0.1"],
        "DNSName": "host-a.example-tailnet.ts.net.",
        "Tags": ["tag:server"], "ExitNodeOption": False, "Online": True}})
    serve = json.dumps({
        "Web": {"host-a:443": {"Handlers": {"/": {"Proxy": "http://127.0.0.1:8080"}}}},
        "AllowFunnel": {"host-a:443": True}})

    def writer(tree: Path, cmd):
        (tree / "testhost").write_text(
            json.dumps({"rc": 0, "stdout": _ts_blob(status, serve)}))

    monkeypatch.setattr(collect_mod.subprocess, "run", _fake_run(writer))

    log = collect_mod.collect_tailscale(host_db, INVENTORY)

    assert log.tailscale_services_upserted == 1
    host = host_db.query(Host).one()
    assert host.tailscale_ipv4 == "100.64.0.1"
    assert host_db.query(TailscaleService).count() == 1


def test_collect_tailscale_skips_host_without_tailscale(host_db: Session, monkeypatch):
    def writer(tree: Path, cmd):
        (tree / "testhost").write_text(
            json.dumps({"rc": 0, "stdout": "status=no-tailscale"}))

    monkeypatch.setattr(collect_mod.subprocess, "run", _fake_run(writer))

    log = collect_mod.collect_tailscale(host_db, INVENTORY)

    assert (log.tailscale_services_upserted or 0) == 0
    assert not log.notes  # a host without tailscale is not an error


def test_collect_tailscale_records_unreachable(host_db: Session, monkeypatch):
    def writer(tree: Path, cmd):
        (tree / "deadhost").write_text(
            json.dumps({"unreachable": True, "msg": "ssh: connect timed out"}))

    monkeypatch.setattr(collect_mod.subprocess, "run", _fake_run(writer))

    log = collect_mod.collect_tailscale(host_db, INVENTORY)

    assert "deadhost" in log.notes


def test_tailscale_probe_cmd_has_no_jinja_braces():
    assert "{{" not in collect_mod._TAILSCALE_PROBE_CMD
    assert "}}" not in collect_mod._TAILSCALE_PROBE_CMD


# --- error handling ---


def test_resolve_inventory_prefers_explicit_and_env(monkeypatch):
    monkeypatch.setattr(collect_mod.settings, "ansible_inventory", "/env/hosts.yml")
    assert resolve_inventory("/explicit.yml") == "/explicit.yml"
    assert resolve_inventory() == "/env/hosts.yml"


def test_resolve_inventory_returns_none_without_a_value(monkeypatch):
    monkeypatch.setattr(collect_mod.settings, "ansible_inventory", None)
    assert resolve_inventory() is None


def test_collect_generates_inventory_from_db(host_db: Session, monkeypatch):
    """With no explicit path and no env var, collect builds an inventory from the DB."""
    monkeypatch.setattr(collect_mod.settings, "ansible_inventory", None)
    seen_inventory: dict = {}

    def writer(tree: Path, cmd):
        # capture the generated inventory path Ansible was pointed at, and its contents
        seen_inventory["path"] = cmd[cmd.index("-i") + 1]
        seen_inventory["text"] = Path(seen_inventory["path"]).read_text()
        (tree / "testhost").write_text(json.dumps({"rc": 0, "stdout": ""}))

    monkeypatch.setattr(collect_mod.subprocess, "run", _fake_run(writer))

    collect_docker(host_db, None)

    assert "testhost" in seen_inventory["text"]
    # temp inventory is cleaned up after the run
    assert not Path(seen_inventory["path"]).exists()


def test_collect_empty_db_without_inventory_raises(db: Session, monkeypatch):
    monkeypatch.setattr(collect_mod.settings, "ansible_inventory", None)

    with pytest.raises(CollectError):
        collect_facts(db, None)


def test_collect_generated_inventory_cleaned_up_on_error(host_db: Session, monkeypatch):
    monkeypatch.setattr(collect_mod.settings, "ansible_inventory", None)
    captured: dict = {}

    def boom(cmd, **kwargs):
        captured["path"] = cmd[cmd.index("-i") + 1]
        raise FileNotFoundError("ansible")

    monkeypatch.setattr(collect_mod.subprocess, "run", boom)

    with pytest.raises(CollectError):
        collect_facts(host_db, None)

    assert not Path(captured["path"]).exists()


def test_collect_facts_missing_ansible_binary(db: Session, monkeypatch):
    def boom(cmd, **kwargs):
        raise FileNotFoundError("ansible")

    monkeypatch.setattr(collect_mod.subprocess, "run", boom)

    with pytest.raises(CollectError):
        collect_facts(db, INVENTORY)
