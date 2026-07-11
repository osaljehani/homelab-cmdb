# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

[Unreleased]: https://github.com/osaljehani/homelab-cmdb/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/osaljehani/homelab-cmdb/releases/tag/v0.1.0
