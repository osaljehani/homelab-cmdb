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
  "Collect now" button on host detail. Covers facts, Docker, K8s topology, Tailscale, and
  listening ports.
- **Container image CVE scanning** Delivered image-side: trivy scans imported as a source,
  stored with history, displayed in web/CLI/MCP. (Host-side scan-images.sh + timer live in
  host-config.)
- **Image UI — stale badge + targeted delete** A non-destructive "stale" badge on `/images` and
  the image detail page (image not in the newest scan run, via `images.is_stale` /
  `images.newest_scan_time` — single-rule first cut, per-source scoping deferred), plus a confirmed
  per-image **Remove** button that reuses `images.delete_image`. No auto-deletion. Design:
  [`docs/design/images-stale-badge-and-delete.md`](design/images-stale-badge-and-delete.md).
- **Stale-host health** `dashboard.fleet_freshness` buckets hosts by data age (fresh/stale/never)
  from `Host.last_seen`, surfaced on the dashboard with a `CMDB_STALE_DAYS` threshold.
- **Dependency / topology graph** `/topology` Cytoscape visualizer
  (`cmdb/domain/services/topology.py`): hosts ↔ containers ↔ compose projects ↔ clusters ↔
  images ↔ subnets ↔ tailnet, with severity coloring and toggleable layers.

## Medium effort

1. **Network / subnet map** Group hosts by subnet derived from `primary_ipv4`/`gateway`;
   show IP allocations and detect duplicate IPs/MACs.
2. **Storage / disk facts** Parse `ansible_devices`/`ansible_mounts` from `raw_facts`;
   capacity per host and low-free-space flags.
3. **Read-only REST/JSON API** Expose hosts/containers/clusters as JSON (FastAPI makes this
   trivial) for scripting and dashboards.
4. **Export / backup** Dump the whole CMDB to YAML/JSON and restore it.
5. **Freeform notes per host** A markdown notes field plus arbitrary key/value custom fields.

## Larger / future

6. **Package & version inventory (CVE-aware)** Track installed packages per host; highlight
   hosts needing security updates.
7. **Authentication** Optional login before exposing the UI beyond localhost.
8. **Agentless live collection** _Delivered for facts + Docker + K8s (see above)._ Remaining:
   scheduled/background runs and concurrent-run locking.
