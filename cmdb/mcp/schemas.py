"""Pydantic output models for the MCP tools.

These serialize SQLAlchemy ORM objects (``from_attributes=True``) into compact,
JSON-friendly shapes. Every tool must build these *inside* an open session so
lazy-loaded relationships (tags, containers, ...) resolve before the session
closes — see ``server.py``.

``raw_facts`` (the large per-host JSON blob) is deliberately excluded from the
default host models to keep tool output small; expose it via a dedicated tool if
ever needed.
"""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, field_validator


class _ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class ContainerOut(_ORMModel):
    name: str
    image: str | None = None
    status: str | None = None
    state: str | None = None
    ports: str | None = None
    compose_project: str | None = None


class PortOut(_ORMModel):
    proto: str | None = None
    address: str | None = None
    port: int | None = None
    process: str | None = None


class TailscaleServiceOut(_ORMModel):
    proto: str | None = None
    port: int | None = None
    target: str | None = None
    funnel: bool | None = None


class HostOut(_ORMModel):
    """Summary view of a host (used by list_hosts)."""

    hostname: str
    fqdn: str | None = None
    machine_id: str
    system_vendor: str | None = None
    product_name: str | None = None
    form_factor: str | None = None
    os_family: str | None = None
    os_distribution: str | None = None
    os_version: str | None = None
    kernel: str | None = None
    arch: str | None = None
    cpu_model: str | None = None
    cpu_cores: int | None = None
    cpu_threads: int | None = None
    memory_mb: int | None = None
    primary_ipv4: str | None = None
    primary_interface: str | None = None
    primary_mac: str | None = None
    gateway: str | None = None
    virt_type: str | None = None
    virt_role: str | None = None
    apparmor_status: str | None = None
    selinux_status: str | None = None
    fips: bool | None = None
    tailscale_ipv4: str | None = None
    tailscale_dns_name: str | None = None
    tailscale_online: bool | None = None
    tags: list[str] = []
    last_seen: datetime | None = None
    created_at: datetime | None = None

    @field_validator("tags", mode="before")
    @classmethod
    def _tag_names(cls, v: Any) -> list[str]:
        return [getattr(t, "name", t) for t in (v or [])]


class HostDetailOut(HostOut):
    """Full view of a single host (used by get_host)."""

    containers: list[ContainerOut] = []
    listening_ports: list[PortOut] = []
    tailscale_services: list[TailscaleServiceOut] = []


class PostureOut(BaseModel):
    hostname: str
    hardened: bool
    mac: str | None = None
    fips: bool | None = None
    issues: list[str] = []


class PostureSummaryOut(BaseModel):
    total: int
    hardened: int
    exposed: int
    fips_on: int
    exposed_hostnames: list[str] = []


class ChangeOut(BaseModel):
    field: str
    old: Any = None
    new: Any = None


class HostHistoryEntry(BaseModel):
    captured_at: datetime | None = None
    initial: bool
    changes: list[ChangeOut] = []


class K8sClusterOut(BaseModel):
    name: str
    description: str | None = None
    node_count: int
    namespaces: list[str] = []


class K8sNodeOut(BaseModel):
    hostname: str
    role: str
    cluster: str


class ImageSummaryOut(_ORMModel):
    ref: str
    expected_noisy: bool = False
    digest: str | None = None
    last_scanned_at: datetime | None = None
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    total: int = 0
    stale: bool = False
    deployment_status: str | None = None  # "running" | "registry-only"
    running_on: list[str] = []  # "host/container" placements


class VulnerabilityOut(_ORMModel):
    vuln_id: str
    severity: str | None = None
    pkg_name: str | None = None
    installed_version: str | None = None
    fixed_version: str | None = None
    title: str | None = None
    target: str | None = None


class ImageDetailOut(BaseModel):
    ref: str
    expected_noisy: bool = False
    scanned_at: datetime | None = None
    trivy_version: str | None = None
    stale: bool = False
    deployment_status: str | None = None  # "running" | "registry-only"
    running_on: list[str] = []  # "host/container" placements
    vulnerabilities: list[VulnerabilityOut] = []


class VulnBucketOut(BaseModel):
    images: int = 0
    scanned_images: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    unknown: int = 0
    total: int = 0


class VulnSummaryOut(VulnBucketOut):
    running: VulnBucketOut = VulnBucketOut()
    registry_only: VulnBucketOut = VulnBucketOut()
