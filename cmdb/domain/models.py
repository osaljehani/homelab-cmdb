from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean, Column, DateTime, ForeignKey, Integer,
    String, Text, Table, UniqueConstraint,
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
    raw_facts = Column(JSON)
    last_seen = Column(DateTime, default=datetime.utcnow)
    created_at = Column(DateTime, default=datetime.utcnow)

    tags = relationship("Tag", secondary=host_tags, back_populates="hosts")
    k8s_nodes = relationship("K8sNode", back_populates="host", cascade="all, delete-orphan")


class Tag(Base):
    __tablename__ = "tags"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)

    hosts = relationship("Host", secondary=host_tags, back_populates="tags")


class K8sNodeRole(str, Enum):
    CONTROL_PLANE = "control-plane"
    WORKER = "worker"
    ETCD = "etcd"


class K8sCluster(Base):
    __tablename__ = "k8s_clusters"

    id = Column(Integer, primary_key=True)
    name = Column(String, unique=True, nullable=False)
    description = Column(Text)

    nodes = relationship("K8sNode", back_populates="cluster", cascade="all, delete-orphan")


class K8sNode(Base):
    __tablename__ = "k8s_nodes"
    __table_args__ = (UniqueConstraint("host_id", "cluster_id"),)

    id = Column(Integer, primary_key=True)
    host_id = Column(Integer, ForeignKey("hosts.id"), nullable=False)
    cluster_id = Column(Integer, ForeignKey("k8s_clusters.id"), nullable=False)
    role = Column(SAEnum(K8sNodeRole), nullable=False)

    host = relationship("Host", back_populates="k8s_nodes")
    cluster = relationship("K8sCluster", back_populates="nodes")


class ImportSource(str, Enum):
    CLI = "cli"
    WEB = "web"


class ImportLog(Base):
    __tablename__ = "import_logs"

    id = Column(Integer, primary_key=True)
    imported_at = Column(DateTime, default=datetime.utcnow)
    source = Column(SAEnum(ImportSource), nullable=False)
    filename = Column(String)
    hosts_upserted = Column(Integer, default=0)
    hosts_failed = Column(Integer, default=0)
    notes = Column(Text)
