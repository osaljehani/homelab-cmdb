# HomeLabCMDB Roadmap

Prioritized feature ideas. The data model already captures a lot that the UI does not yet
surface   several of these are low effort because the data is already there.

## Delivered

- **Docker container inventory**   `Container` model, `docker_import` service,
  `/containers` page, host-detail Containers card, `cmdb import docker`,
  `scripts/docker-export.sh`. Replace-on-import per host so removed containers disappear.
- **Change history / diff**   `HostSnapshot` captured on each import (only when meaningful
  fields change), per-host Change History timeline on the detail page, `cmdb hosts history`.

## High value, low effort (data already in the model)

1. **Security posture view**   Surface `apparmor_status`, `selinux_status`, `fips` (already on
   `Host`) as a dashboard panel and a Security column on the hosts list. Flag hosts with
   AppArmor/SELinux disabled or FIPS off. No schema change needed.
2. **Stale-host health**   Use `Host.last_seen` to compute online/stale badges (e.g. not seen
   in N days). Add a dashboard "stale hosts" count and a badge on the hosts list/detail.
   Configurable threshold via env var. No schema change needed.

## Medium effort

3. **Network / subnet map**   Group hosts by subnet derived from `primary_ipv4`/`gateway`;
   show IP allocations and detect duplicate IPs/MACs.
4. **Storage / disk facts**   Parse `ansible_devices`/`ansible_mounts` from `raw_facts` into a
   table; capacity per host and low-free-space flags.
5. **Read-only REST/JSON API**   Expose hosts/containers/clusters as JSON (FastAPI makes this
   trivial) for scripting and dashboards.
6. **Export / backup**   Dump the whole CMDB to YAML/JSON and restore it.
7. **Freeform notes per host**   A markdown notes field plus arbitrary key/value custom fields.

## Larger / future

8. **Package & version inventory (CVE-aware)**   Track installed packages per host; highlight
   hosts needing security updates.
9. **Authentication**   Optional login before exposing the UI beyond localhost.
10. **Dependency / topology graph**   Visualize hosts ↔ containers ↔ clusters ↔ services.
11. **Agentless live collection**   Pull Ansible facts / `docker ps` over SSH on demand instead
    of uploading files.
