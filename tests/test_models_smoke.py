from cmdb.domain.models import Host, Tag, K8sCluster, K8sNode, ImportLog, K8sNodeRole, ImportSource


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
