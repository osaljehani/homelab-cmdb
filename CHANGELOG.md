# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- Topology view is now a collapsible drill-down. The graph is a compound hierarchy
  (`host → compose → container` and `cluster → namespace → workload`) and loads collapsed to a
  skeleton of hosts and clusters; double-click a group (or its `+` cue) to expand it. Clusters are
  now compound containers holding their namespaces rather than hexagons linked by an edge, so
  collapsing a cluster folds an entire Kubernetes layer to a single node — keeping the view legible
  at 60+ workloads. Group labels show a member count (e.g. `security ·5`).
- Images and vulnerabilities move off-canvas by default: instead of an image node plus edge per
  container/workload, each leaf carries a worst-severity ring (colored by critical/high) and its CVE
  counts appear in the detail panel. A collapsed group containing a hidden critical still shows the
  red ring. The Kubernetes layer is now on by default; the legacy image nodes remain available on
  the off-by-default "Images & vulns" layer.

### Added

- Topology filter bar: free-text node search, a host/cluster scope selector, and a "critical only"
  toggle. Any active filter auto-expands so matches deep inside a group are reachable, and clearing
  every filter collapses back to the skeleton. Added "Expand all" / "Collapse all" controls
  alongside "Re-layout".

## [0.3.1] - 2026-07-21

### Security

- Re-based the container image onto `gcr.io/distroless/cc-debian12` (from `python:3.12-slim`),
  removing the Debian `perl-base` (CVE-2026-13221, -42496, -8376) and `openssh-client`
  (CVE-2026-60002) CRITICAL findings that had no upstream fix. Trivy now reports 0 CRITICAL / 0 HIGH
  for the image, down from 4 CRITICAL.

### Changed

- `cmdb collect` now drives Ansible over the pure-Python paramiko connection plugin instead of the
  openssh `ssh` binary, so the distroless runtime needs no `openssh-client`. `ansible-core` is pinned
  `<2.21` (2.21 removed the paramiko connection plugin) and `paramiko` joins the `collect` dependency
  group. The image bundles a self-contained CPython 3.13 and `uv`, so the entrypoint and the
  image-scan importer (`uv run cmdb import trivy`) are unchanged.

## [0.3.0] - 2026-07-21

### Added

- Topology view now renders a Kubernetes layer behind a new "Kubernetes" toggle (off by default):
  namespace boxes under each cluster, each holding one node per distinct workload with replicas
  collapsed to a count (e.g. `falco ×3`), plus severity-colored workload→image edges mirroring the
  Docker container→image styling. Images that run only in Kubernetes now appear on the map too, so
  their vulnerabilities are visible; such image nodes hide when either the Images or the Kubernetes
  layer is turned off. Namespaces link to the cluster hexagon by an edge rather than nesting, so the
  hexagon keeps its shape. The detail panel gains cluster / namespace / replica rows.

## [0.2.1] - 2026-07-21

### Fixed

- Trivy import now counts one finding per (CVE, package, installed version) per image instead of
  one per report Result. Scanners that emit one Result per binary (e.g. standalone trivy scanning
  Go-heavy images) previously multiplied the same CVE by the number of binaries containing it,
  inflating severity rollups by hundreds versus feeds that flatten to a single Result (such as the
  Zot registry transform) — so totals sawtoothed depending on which feed scanned an image last.

## [0.2.0] - 2026-07-20

### Added

- Immutable daily vulnerability snapshots (`vuln_snapshots` table): every trivy import freezes
  per-image severity rollups plus that day's running/noisy classification. The dashboard's 30-day
  trend now reads snapshots instead of recomputing from current state, so remediating a CVE by
  deleting the old image keeps past trend points intact while today's point drops immediately.
  The migration backfills the last 30 days from existing scan history automatically at startup;
  `cmdb db backfill-vuln-snapshots` re-runs the backfill after importing historical scan files.
  Docker/K8s inventory imports and `/collect` runs also refresh today's snapshot, so a stopped
  or replaced container moves today's trend point at the next collection.

### Changed

- Deleting an image (web / CLI / MCP) still removes its scans and per-CVE findings, but daily
  totals already captured in the vulnerability trend are now kept; the confirmation copy on all
  three surfaces says so.


## [0.1.0] - 2026-07-11

### Added

- Ansible-fed import of host facts: identity, OS, hardware, network, storage, security
  (AppArmor/SELinux/FIPS), and virtualization, keyed on `machine_id` so re-imports update in place.
- Docker container import (image, state, ports, compose project); re-import replaces a host's
  container set so removed containers disappear.
- Kubernetes import: clusters, nodes (by role), and namespaces via `kubectl`.
- Tailscale import: per-host tailnet identity (IP, MagicDNS name, tags, exit-node, online state)
  and serve/funnel-exposed services.
- Listening port import (proto, address, port, owning process) via `ss`.
- Agentless on-demand collection: pull facts, Docker, Kubernetes, Tailscale, and port state live
  over SSH (via Ansible) from the CMDB host, with inventory auto-generated from the database.
- Web UI: dashboard (severity breakdown, 30-day vulnerability trend, security posture, fleet
  freshness, recent changes, OS mix — server-rendered SVG/CSS, no JS chart library).
- Web UI: interactive topology visualizer (Cytoscape) — hosts/containers nested by compose
  project, Kubernetes clusters, subnets, tailnet, exposure rings, container→image edges colored by
  severity, with client-side layer toggles.
- Web UI: network map view.
- Web UI: image vulnerability view — trivy scan ingestion, per-image scan history, severity
  counts, browsable findings, and expected-noisy image flagging to keep rollups meaningful.
- Web UI: global search across hosts, containers, and images by name, IP, MagicDNS name, or
  image ref.
- Change history: per-import field snapshots with a per-host diff timeline, skipping volatile
  fields like uptime.
- Host tags, freeform notes, and custom fields.
- MCP server (23 tools) for querying and managing the CMDB from LLM clients over stdio.
- Read-only REST API (`/api/v1/{hosts,containers,clusters,images,vuln-summary}`) sharing response
  models with the MCP server, with interactive docs at `/docs`.
- Export/restore: dump the whole CMDB to JSON/YAML and restore into an empty database.
- Generate Ansible inventory (YAML/INI) and SSH config from the current inventory.
- Demo mode (`cmdb demo`, and the `cmdb-demo` Docker Compose profile): seeds a fictional sample
  fleet into a throwaway database so the UI can be explored with no setup.

[Unreleased]: https://github.com/osaljehani/homelab-cmdb/compare/v0.2.0...HEAD
[0.2.0]: https://github.com/osaljehani/homelab-cmdb/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/osaljehani/homelab-cmdb/releases/tag/v0.1.0
