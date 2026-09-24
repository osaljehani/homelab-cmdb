import os

# Before anything imports cmdb.config. Settings.resolved_secret_key() generates
# and persists a key beside the DB when CMDB_SECRET_KEY is still the shipped
# default and a session-bearing mode is active -- which is the correct
# production behaviour, but in a test run it would drop a .secret_key file in
# the repo just by importing cmdb.web.app.
os.environ.setdefault("CMDB_SECRET_KEY", "test-secret-key-not-a-real-one")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from cmdb.domain.models import Base

_SAMPLE_FACTS = {
    "ansible_facts": {
        "ansible_hostname": "testhost",
        "ansible_fqdn": "testhost.local",
        "ansible_machine_id": "aabbccdd11223344aabbccdd11223344",
        "ansible_system_vendor": "ACME Corp",
        "ansible_product_name": "Generic Server",
        "ansible_product_version": "1.0",
        "ansible_product_serial": "SN-000000",
        "ansible_form_factor": "Tower",
        "ansible_os_family": "Debian",
        "ansible_distribution": "Ubuntu",
        "ansible_distribution_version": "22.04",
        "ansible_distribution_release": "jammy",
        "ansible_kernel": "5.15.0-91-generic",
        "ansible_pkg_mgr": "apt",
        "ansible_architecture": "x86_64",
        "ansible_processor": ["0", "GenuineIntel", "Intel(R) Xeon(R) CPU E5-2670 @ 2.60GHz"],
        "ansible_processor_cores": 8,
        "ansible_processor_vcpus": 16,
        "ansible_memtotal_mb": 32768,
        "ansible_virtualization_type": "kvm",
        "ansible_virtualization_role": "host",
        "ansible_service_mgr": "systemd",
        "ansible_uptime_seconds": 86400,
        "ansible_fips": False,
        "ansible_bios_vendor": "ACME BIOS",
        "ansible_bios_version": "1.0.0",
        "ansible_bios_date": "01/01/2023",
        "ansible_board_vendor": "ACME Corp",
        "ansible_board_name": "Generic Board",
        "ansible_board_serial": "BRD-000000",
        "ansible_default_ipv4": {
            "address": "192.168.1.10",
            "interface": "eth0",
            "macaddress": "aa:bb:cc:dd:ee:ff",
            "gateway": "192.168.1.1",
        },
        "ansible_apparmor": {"status": "enabled"},
        "ansible_selinux": {"status": "disabled"},
        "ansible_devices": {},
    }
}


@pytest.fixture(autouse=True)
def default_auth_mode(monkeypatch):
    """Keep the pre-authentication behaviour as the suite-wide baseline.

    CMDB_AUTH_MODE defaults to `local`, so without this every web test would get
    a 302 to /login instead of the page it asserts on. Tests that are *about*
    authentication opt back in by setting the mode themselves (see the gate_app
    fixture in tests/test_auth.py), which wins because it happens inside the
    test body.
    """
    from cmdb.config import settings

    monkeypatch.setattr(settings, "auth_mode", "none", raising=False)


@pytest.fixture
def db() -> Session:
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    Base.metadata.drop_all(engine)


@pytest.fixture
def host_facts() -> dict:
    import copy
    return copy.deepcopy(_SAMPLE_FACTS)


@pytest.fixture
def host_facts_alt() -> dict:
    """Same machine_id as host_facts, different hostname for upsert tests."""
    import copy
    data = copy.deepcopy(_SAMPLE_FACTS)
    data["ansible_facts"]["ansible_hostname"] = "testhost-renamed"
    return data
