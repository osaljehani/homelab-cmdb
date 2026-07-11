from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    Table,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, relationship
from sqlalchemy.types import JSON, Enum as SAEnum


class Base(DeclarativeBase):
    pass


host_tags = Table(
    "host_tags",
    Base.metadata,
    Column("host_id", Integer, ForeignKey("hosts.id"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id"), primary_key=True),
)


class Host(Base):
    __tablename__ = "hosts"

    id = Column(Integer, primary_key=True)
    machine_id = Column(String, unique=True, nullable=False, index=True)
    hostname = Column(String, nullable=False)
    fqdn = Column(String)
    system_vendor = Column(String)
    product_name = Column(String)
    product_version = Column(String)
    serial = Column(String)
    form_factor = Column(String)
    os_family = Column(String)
    os_distribution = Column(String)
    os_version = Column(String)
    os_release = Column(String)
    kernel = Column(String)
    pkg_mgr = Column(String)
    arch = Column(String)
    cpu_model = Column(String)
    cpu_cores = Column(Integer)
    cpu_threads = Column(Integer)
    memory_mb = Column(Integer)
    primary_ipv4 = Column(String)
    primary_interface = Column(String)
    primary_mac = Column(String)
    gateway = Column(String)
    virt_type = Column(String)
    virt_role = Column(String)
    apparmor_status = Column(String)
    selinux_status = Column(String)
    fips = Column(Boolean)
    bios_vendor = Column(String)
    bios_version = Column(String)
    bios_date = Column(String)
    board_vendor = Column(String)
    board_name = Column(String)
    board_serial = Column(String)
    service_mgr = Column(String)
    uptime_seconds = Column(Integer)
    tailscale_ipv4 = Column(String)
    tailscale_dns_name = Column(String)
    tailscale_tags = Column(String)
    tailscale_exit_node = Column(Boolean)
    tailscale_online = Column(Boolean)
    raw_facts = Column(JSON)
    # Operator-maintained, never touched by imports: freeform markdown notes
    # and arbitrary key/value custom fields.
    notes = Column(Text)
    custom_fields = Column(JSON)
    last_seen = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    tags = relationship("Tag", secondary=host_tags, back_populates="hosts")
    k8s_nodes = relationship(
        "K8sNode", back_populates="host", cascade="all, delete-orphan"
    )
    containers = relationship(
        "Container", back_populates="host", cascade="all, delete-orphan"
    )
    tailscale_services = relationship(
        "TailscaleService", back_populates="host", cascade="all, delete-orphan"
    )
    listening_ports = relationship(
        "ListeningPort", back_populates="host", cascade="all, delete-orphan"
    )
    snapshots = relationship(
        "HostSnapshot",
        back_populates="host",
        cascade="all, delete-orphan",
        order_by="HostSnapshot.captured_at.desc()",
    )


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    hosts = relationship("Host", secondary=host_tags, back_populates="tags")


class Container(Base):
    __tablename__ = "containers"
    __table_args__ = (UniqueConstraint("host_id", "name"),)

    id = Column(Integer, primary_key=True)
    host_id = Column(Integer, ForeignKey("hosts.id"), nullable=False)
    name = Column(String, nullable=False)
    image = Column(String)
    status = Column(String)
    state = Column(String)
    ports = Column(String)
    compose_project = Column(String)
    last_seen = Column(DateTime, default=datetime.utcnow)

    host = relationship("Host", back_populates="containers")


class Image(Base):
    __tablename__ = "images"

    id = Column(Integer, primary_key=True)
    ref = Column(String, unique=True, nullable=False, index=True)
    digest = Column(String)
    expected_noisy = Column(Boolean, default=False, nullable=False)
    first_seen = Column(DateTime, default=datetime.utcnow)
    last_scanned_at = Column(DateTime)

    scans = relationship(
        "ImageScan",
        back_populates="image",
        cascade="all, delete-orphan",
        order_by="ImageScan.scanned_at.desc()",
    )


class ImageScan(Base):
    __tablename__ = "image_scans"

    id = Column(Integer, primary_key=True)
    image_id = Column(Integer, ForeignKey("images.id"), nullable=False)
    scanned_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    trivy_version = Column(String)
    # Runtime this scan came from: "docker" (runtime daemon scan) or "kubernetes"
    # (zot registry scan of containerd-pulled images). Derived from the scan
    # envelope on import; nullable for rows imported before this column existed.
    source = Column(String)
    # Raw envelope "host" label — which machine/scanner produced the run.
    # Provenance only, distinct from the derived `source`; nullable for rows
    # imported before this column existed or envelopes that omit it.
    host = Column(String)
    import_log_id = Column(Integer, ForeignKey("import_logs.id"), nullable=True)
    critical = Column(Integer, default=0)
    high = Column(Integer, default=0)
    medium = Column(Integer, default=0)
    low = Column(Integer, default=0)
    unknown = Column(Integer, default=0)
    total = Column(Integer, default=0)

    image = relationship("Image", back_populates="scans")
    vulnerabilities = relationship(
        "Vulnerability", back_populates="scan", cascade="all, delete-orphan"
    )


class Vulnerability(Base):
    __tablename__ = "vulnerabilities"

    id = Column(Integer, primary_key=True)
    scan_id = Column(Integer, ForeignKey("image_scans.id"), nullable=False)
    vuln_id = Column(String, nullable=False)
    pkg_name = Column(String)
    installed_version = Column(String)
    fixed_version = Column(String)
    severity = Column(String)
    title = Column(Text)
    target = Column(String)

    scan = relationship("ImageScan", back_populates="vulnerabilities")


class TailscaleService(Base):
    __tablename__ = "tailscale_services"

    id = Column(Integer, primary_key=True)
    host_id = Column(Integer, ForeignKey("hosts.id"), nullable=False)
    proto = Column(String)
    port = Column(Integer)
    target = Column(String)
    funnel = Column(Boolean, default=False)

    host = relationship("Host", back_populates="tailscale_services")


class ListeningPort(Base):
    __tablename__ = "listening_ports"

    id = Column(Integer, primary_key=True)
    host_id = Column(Integer, ForeignKey("hosts.id"), nullable=False)
    proto = Column(String)
    address = Column(String)
    port = Column(Integer)
    process = Column(String)

    host = relationship("Host", back_populates="listening_ports")


class HostSnapshot(Base):
    __tablename__ = "host_snapshots"

    id = Column(Integer, primary_key=True)
    host_id = Column(Integer, ForeignKey("hosts.id"), nullable=False)
    import_log_id = Column(Integer, ForeignKey("import_logs.id"), nullable=True)
    captured_at = Column(DateTime, default=datetime.utcnow)
    fields = Column(JSON, nullable=False)

    host = relationship("Host", back_populates="snapshots")


class K8sNodeRole(str, Enum):
    CONTROL_PLANE = "control-plane"
    WORKER = "worker"
    ETCD = "etcd"


class K8sCluster(Base):
    __tablename__ = "k8s_clusters"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)

    nodes = relationship(
        "K8sNode", back_populates="cluster", cascade="all, delete-orphan"
    )
    namespaces = relationship(
        "K8sNamespace", back_populates="cluster", cascade="all, delete-orphan"
    )
    workloads = relationship(
        "K8sWorkload", back_populates="cluster", cascade="all, delete-orphan"
    )


class K8sNode(Base):
    __tablename__ = "k8s_nodes"
    __table_args__ = (UniqueConstraint("host_id", "cluster_id"),)

    id = Column(Integer, primary_key=True)
    host_id = Column(Integer, ForeignKey("hosts.id"), nullable=False)
    cluster_id = Column(Integer, ForeignKey("k8s_clusters.id"), nullable=False)
    role = Column(SAEnum(K8sNodeRole), nullable=False)

    host = relationship("Host", back_populates="k8s_nodes")
    cluster = relationship("K8sCluster", back_populates="nodes")


class K8sNamespace(Base):
    __tablename__ = "k8s_namespaces"
    __table_args__ = (UniqueConstraint("cluster_id", "name"),)

    id = Column(Integer, primary_key=True)
    cluster_id = Column(Integer, ForeignKey("k8s_clusters.id"), nullable=False)
    name = Column(String, nullable=False)

    cluster = relationship("K8sCluster", back_populates="namespaces")


class K8sWorkload(Base):
    """A running pod container observed by the k8s collection probe.

    Replaced wholesale per cluster on every import (like Container per host),
    so rows always reflect the latest `kubectl get pods -A` snapshot.
    """

    __tablename__ = "k8s_workloads"
    __table_args__ = (
        UniqueConstraint("cluster_id", "namespace", "pod_name", "container_name"),
    )

    id = Column(Integer, primary_key=True)
    cluster_id = Column(Integer, ForeignKey("k8s_clusters.id"), nullable=False)
    namespace = Column(String, nullable=False)
    pod_name = Column(String, nullable=False)
    container_name = Column(String, nullable=False)
    image = Column(String, nullable=False)  # raw pod-spec ref
    # canonical_ref(image) — the join key to Image.ref. NULL for digest-only
    # refs (repo@sha256:...) where defaulting to :latest would fabricate a
    # match against a possibly different build.
    image_canonical = Column(String, index=True)
    last_seen = Column(DateTime, default=datetime.utcnow)

    cluster = relationship("K8sCluster", back_populates="workloads")


class ImportSource(str, Enum):
    CLI = "cli"
    WEB = "web"
    COLLECT = "collect"


class ImportLog(Base):
    __tablename__ = "import_logs"

    id = Column(Integer, primary_key=True)
    imported_at = Column(DateTime, default=datetime.utcnow)
    source = Column(SAEnum(ImportSource), nullable=False)
    filename = Column(String)
    hosts_upserted = Column(Integer, default=0)
    hosts_failed = Column(Integer, default=0)
    containers_upserted = Column(Integer, nullable=True)
    k8s_clusters_upserted = Column(Integer, nullable=True)
    k8s_nodes_upserted = Column(Integer, nullable=True)
    k8s_namespaces_upserted = Column(Integer, nullable=True)
    k8s_workloads_upserted = Column(Integer, nullable=True)
    tailscale_services_upserted = Column(Integer, nullable=True)
    listening_ports_upserted = Column(Integer, nullable=True)
    images_scanned = Column(Integer, nullable=True)
    vulnerabilities_upserted = Column(Integer, nullable=True)
    notes = Column(Text)
