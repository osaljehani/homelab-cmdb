from cmdb.domain.models import Host, ImportLog, K8sNodeRole, ImportSource


def test_host_model_has_machine_id():
    h = Host(machine_id="abc", hostname="test")
    assert h.machine_id == "abc"


def test_k8s_node_role_enum():
    assert K8sNodeRole.CONTROL_PLANE == "control-plane"
    assert K8sNodeRole.WORKER == "worker"
    assert K8sNodeRole.ETCD == "etcd"


def test_import_source_enum():
    assert ImportSource.CLI == "cli"
    assert ImportSource.WEB == "web"


def test_tailscale_and_ports_models(db):
    from cmdb.domain.models import (
        Host, ListeningPort, TailscaleService, ImportSource,
    )

    host = Host(machine_id="mid-1", hostname="host-a",
                tailscale_ipv4="100.64.0.1",
                tailscale_dns_name="host-a.example-tailnet.ts.net",
                tailscale_tags="tag:server", tailscale_exit_node=False,
                tailscale_online=True)
    db.add(host)
    db.flush()

    db.add(ListeningPort(host_id=host.id, proto="tcp", address="0.0.0.0",
                         port=22, process="sshd"))
    db.add(TailscaleService(host_id=host.id, proto="https", port=443,
                            target="127.0.0.1:8080", funnel=True))
    db.add(ImportLog(source=ImportSource.COLLECT, filename="x",
                     tailscale_services_upserted=1, listening_ports_upserted=1))
    db.flush()

    h = db.query(Host).filter_by(machine_id="mid-1").one()
    assert h.tailscale_ipv4 == "100.64.0.1"
    assert h.listening_ports[0].process == "sshd"
    assert h.tailscale_services[0].funnel is True
