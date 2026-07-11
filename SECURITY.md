# Security Policy

## Supported versions

Only the latest release is supported. If you're not on the most recent tag, please upgrade before
reporting a security issue.

## Reporting a vulnerability

Please report security issues privately via
[GitHub Security Advisories](https://github.com/osaljehani/homelab-cmdb/security/advisories/new)
rather than opening a public issue. There is no dedicated security email — use the advisory form.

You should get an initial response within a few days. This is a spare-time homelab project, so
please be patient with turnaround on fixes.

## Deployment posture

HomeLabCMDB is designed for trusted-network use, not public exposure:

- **No authentication, by design.** The web UI and the read-only REST API have no login, no API
  keys, and no access control. Anyone who can reach the port can read (and, via the web UI,
  modify) your inventory.
- **Deploy on a trusted LAN**, or put it behind a reverse proxy that adds authentication
  (e.g. Authelia, Tailscale Serve with access control, Basic Auth in nginx/Caddy) if it needs to be
  reachable from anywhere less trusted than your homelab network.
- **Treat the database and exports as sensitive.** `data/cmdb.db` and anything produced by
  `cmdb export` / the Generate page contain your full infrastructure inventory — hostnames, IPs,
  OS/package details, container and Kubernetes topology, Tailscale identity, and vulnerability
  scan results. Back these up like you would any other credential-adjacent data, and don't commit
  them to a public repo.

Vulnerability reports about the lack of built-in authentication are a known trade-off, not a bug —
but reports about anything that breaks out of that trust boundary (e.g. path traversal, SSRF,
injection) are very welcome.
