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
- **Container image CVE scanning** Delivered image-side: trivy scans imported as a source,
  stored with history, displayed in web/CLI/MCP. (Host-side scan-images.sh + timer live in
  host-config.)

## High value, low effort (data already in the model)

1. **Stale-host health** Use `Host.last_seen` to compute online/stale badges (e.g. not seen
   in N days). Add a dashboard "stale hosts" count and a badge on the hosts list/detail.
   Configurable threshold via env var. No schema change needed.
2. **Image UI — stale badge + targeted delete** Bring the `cmdb images rm` / `delete_image`
   capability into the web UI: a "not seen in last scan" badge on `/images` (find) plus a
   confirmed per-image delete that reuses `images.delete_image` (act). Explicitly no
   auto-deletion. Full design: [`docs/design/images-stale-badge-and-delete.md`](design/images-stale-badge-and-delete.md).

## Medium effort

3. **Network / subnet map** Group hosts by subnet derived from `primary_ipv4`/`gateway`;
   show IP allocations and detect duplicate IPs/MACs.
4. **Storage / disk facts** Parse `ansible_devices`/`ansible_mounts` from `raw_facts` into a
   table; capacity per host and low-free-space flags.
5. **Read-only REST/JSON API** Expose hosts/containers/clusters as JSON (FastAPI makes this
   trivial) for scripting and dashboards.
6. **Export / backup** Dump the whole CMDB to YAML/JSON and restore it.
7. **Freeform notes per host** A markdown notes field plus arbitrary key/value custom fields.

## Larger / future

8. **Package & version inventory (CVE-aware)** Track installed packages per host; highlight
   hosts needing security updates.
9. **Authentication** Optional login before exposing the UI beyond localhost.
10. **Dependency / topology graph** Visualize hosts ↔ containers ↔ clusters ↔ services.
11. **Agentless live collection** _Delivered for facts + Docker (see above)._ Remaining:
    on-demand K8s topology collection, scheduled/background runs, and concurrent-run locking.
