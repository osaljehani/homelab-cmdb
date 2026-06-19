# HomeLabCMDB Roadmap

Prioritized feature ideas. The data model already captures a lot that the UI does not yet
surface several of these are low effort because the data is already there.

## Delivered

- **Docker container inventory** `Container` model, `docker_import` service,
  `/containers` page, host-detail Containers card, `cmdb import docker`,
  `scripts/docker-export.sh`. Replace-on-import per host so removed containers disappear.
- **Change history / diff** `HostSnapshot` captured on each import (only when meaningful
  fields change), per-host Change History timeline on the detail page, `cmdb hosts history`.
- **Security posture view** `host_posture`/`posture_summary` helpers
  (`cmdb/domain/services/security.py`) surface `apparmor_status`/`selinux_status`/`fips` as a
  dashboard Security Posture panel, a Security column on the hosts list, and a posture line in
  `cmdb hosts show`. A host is hardened when AppArmor is enabled or SELinux enforcing/enabled,
  exposed otherwise; FIPS is informational.
- **On-demand collection (agentless)** `cmdb/domain/services/collect.py` drives the
  `ansible` binary from the CMDB host over SSH (`-m setup` for facts, `-m shell` for
  `docker ps`) against a configured inventory and feeds results into the existing import
  pipeline. Surfaced as `cmdb collect facts|docker|all`, a `/collect` web page, and a
  "Collect now" button on host detail. Facts + Docker only for now (see #7 for K8s).

## High value, low effort (data already in the model)

1. **Stale-host health** Use `Host.last_seen` to compute online/stale badges (e.g. not seen
   in N days). Add a dashboard "stale hosts" count and a badge on the hosts list/detail.
   Configurable threshold via env var. No schema change needed.

## Medium effort

2. **Network / subnet map** Group hosts by subnet derived from `primary_ipv4`/`gateway`;
   show IP allocations and detect duplicate IPs/MACs.
3. **Storage / disk facts** Parse `ansible_devices`/`ansible_mounts` from `raw_facts` into a
   table; capacity per host and low-free-space flags.
4. **Read-only REST/JSON API** Expose hosts/containers/clusters as JSON (FastAPI makes this
   trivial) for scripting and dashboards.
5. **Export / backup** Dump the whole CMDB to YAML/JSON and restore it.
6. **Freeform notes per host** A markdown notes field plus arbitrary key/value custom fields.

## Larger / future

7. **Package & version inventory (CVE-aware)** Track installed packages per host; highlight
   hosts needing security updates.
8. **Authentication** Optional login before exposing the UI beyond localhost.
9. **Dependency / topology graph** Visualize hosts ↔ containers ↔ clusters ↔ services.
10. **Agentless live collection** _Delivered for facts + Docker (see above)._ Remaining:
    on-demand K8s topology collection, scheduled/background runs, and concurrent-run locking.
