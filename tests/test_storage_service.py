from cmdb.domain.models import Host
from cmdb.domain.services.storage import fleet_storage, host_storage


def _facts(mounts=(), devices=None):
    return {"ansible_mounts": list(mounts), "ansible_devices": devices or {}}


def _mount(mount="/", device="/dev/sda1", fstype="ext4", total=100, available=40):
    return {
        "mount": mount,
        "device": device,
        "fstype": fstype,
        "size_total": total,
        "size_available": available,
    }


def _host(db, i, facts=None):
    h = Host(machine_id=f"m{i:032d}", hostname=f"host-{i}", raw_facts=facts)
    db.add(h)
    db.flush()
    return h


def test_host_storage_computes_used_pct(db):
    h = _host(db, 1, _facts([_mount(total=100, available=40)]))
    st = host_storage(h)
    assert st["mounts"] == [
        {
            "mount": "/",
            "device": "/dev/sda1",
            "fstype": "ext4",
            "size_total": 100,
            "size_available": 40,
            "used_pct": 60,
        }
    ]


def test_host_storage_filters_pseudo_filesystems(db):
    h = _host(
        db,
        1,
        _facts(
            [
                _mount(),
                _mount(mount="/snap/core", device="/dev/loop3", fstype="squashfs"),
                _mount(mount="/dev/shm", fstype="tmpfs"),
                _mount(mount="/zero", total=0, available=0),
            ]
        ),
    )
    assert [m["mount"] for m in host_storage(h)["mounts"]] == ["/"]


def test_host_storage_devices_skip_loops(db):
    h = _host(
        db,
        1,
        _facts(
            devices={
                "nvme0n1": {"model": "WD Black", "size": "931 GB", "rotational": "0"},
                "loop0": {"model": None, "size": "63 MB", "rotational": "0"},
                "ram0": {"model": None, "size": "4 MB", "rotational": "0"},
            }
        ),
    )
    devs = host_storage(h)["devices"]
    assert [d["name"] for d in devs] == ["nvme0n1"]
    assert devs[0] == {
        "name": "nvme0n1",
        "model": "WD Black",
        "size": "931 GB",
        "rotational": False,
    }


def test_host_storage_missing_facts_is_empty(db):
    h = _host(db, 1, None)
    assert host_storage(h) == {"devices": [], "mounts": []}


def test_fleet_storage_warnings_sorted_worst_first(db):
    _host(db, 1, _facts([_mount(total=100, available=40)]))  # 60% used
    _host(db, 2, _facts([_mount(total=100, available=5)]))  # 95% used
    _host(db, 3, _facts([_mount(total=100, available=2)]))  # 98% used

    fleet = fleet_storage(db, warn_pct=90)

    assert [w["hostname"] for w in fleet["warnings"]] == ["host-3", "host-2"]
    assert fleet["warnings"][0]["used_pct"] == 98
    assert fleet["warnings"][0]["mount"] == "/"


def test_fleet_storage_no_warnings_below_threshold(db):
    _host(db, 1, _facts([_mount(total=100, available=40)]))
    assert fleet_storage(db, warn_pct=90)["warnings"] == []
