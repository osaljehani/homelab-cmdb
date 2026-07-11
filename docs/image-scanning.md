# Image vulnerability scanning

HomeLabCMDB **ingests and displays** container-image vulnerability scans; it does
**not** run scanners itself. Something external produces [trivy](https://trivy.dev)
scan output, wraps it in the envelope contract below, and hands it to CMDB. There is
no scheduler, poller, or watcher inside CMDB — scans are always pushed in.

This page documents the contract and ships example automation so you can wire up your
own scanning pipeline.

## Mental model: the images view is scan-driven

The **Containers**/host inventory (from Ansible/Docker/K8s collection) and the
**Images** view (from trivy scans) are separate datasets. A running container does
**not** appear under `/images` until its image has been **scanned and imported**. So
if a container shows up in the inventory but not under Images, it simply hasn't been
scanned yet — scan it (or wait for the next scheduled run) and it will appear.

## The envelope contract

The single coupling point between any scanner and CMDB is one JSON file — the
"envelope":

```json
{
  "host": "my-docker-host",
  "scanned_at": "2026-07-03T04:00:00Z",
  "trivy_version": "0.72.0",
  "images": [
    { /* one `trivy image --format json` report, verbatim */ },
    { /* ...one per image... */ }
  ]
}
```

- `host` — a free-text label for the scan source, **persisted per scan** (shown in
  the image detail's scan history and in the MCP `list_image_scans` output). It also
  feeds the docker-vs-kubernetes `source` derivation; use it to keep your own scan
  sources distinguishable.
- `scanned_at` — ISO-8601 UTC timestamp (a trailing `Z` is accepted). Falls back to
  "now" if missing.
- `trivy_version` — free-text; stored per scan.
- `images` — **required**; a list of native `trivy image --format json` reports.

From each report, CMDB's importer
([`cmdb/domain/services/trivy_import.py`](../cmdb/domain/services/trivy_import.py),
`import_scan_run` → `_ingest_report`) reads:

| Report field | Used for |
|---|---|
| `ArtifactName` | image `ref` (**required**; the unique key per image) |
| `Metadata.RepoDigests[0]` (else `Metadata.ImageID`) | image `digest` |
| `Results[].Vulnerabilities[]` | per-CVE rows (`VulnerabilityID`, `PkgName`, `InstalledVersion`, `FixedVersion`, `Severity`, `Title`) and severity roll-up counts |

Each import creates a new `ImageScan` row (full time-series history is retained), and
images are upserted by `ref`, so re-scanning the same image updates it rather than
duplicating it. A single envelope file may also be a JSON **list** of envelopes.

## Feeding CMDB a scan

There are four ways to get an envelope into CMDB; all use the same importer.

1. **Web upload** — open `/import` and upload the envelope JSON under the trivy
   source. Best for one-off / manual scans.
2. **CLI** — `cmdb import trivy <path>` where `<path>` is an envelope file **or a
   directory** of them:
   ```bash
   uv run cmdb import trivy ./data/scans/20260703T040000Z.json
   uv run cmdb import trivy ./data/scans/          # import a whole directory
   ```
3. **HTTP upload** — POST the envelope to the same endpoint the web upload uses.
   This is the cross-host path: any machine on the network can push a scan without
   running the CMDB container:
   ```bash
   curl -fsS -F "files=@scan.json" http://cmdb.example.lan:8080/import/upload/trivy
   ```
   The endpoint is **unauthenticated** — keep the CMDB port LAN/tailnet-only.
4. **Scheduled script** — the automated path (below), which builds the envelope and
   feeds it via the CLI (`cmdb_feed=exec`) or the HTTP upload (`cmdb_feed=http`).

## Automating scans (example scripts)

Two ready-to-use example scripts live in [`scripts/`](../scripts). Both enumerate
their targets **dynamically on every run** (nothing is hardcoded), build a fresh
envelope, and import it — then prune old envelope files (`scan_retention_days`).

- [`scripts/trivy-scan.sh`](../scripts/trivy-scan.sh) — the common case. Scans the
  images backing the host's **running Docker containers** (`docker ps`) with a
  pinned, ephemeral `aquasec/trivy` container, then imports via the running CMDB
  container. Requires `docker` + `jq`.
- [`scripts/trivy-scan-registry.sh`](../scripts/trivy-scan-registry.sh) — **optional**
  variant for anyone running an OCI registry with an embedded CVE API (e.g. Zot with
  its Trivy extension). Scans every image in the **registry catalog** instead of the
  Docker daemon — useful for images that only run under Kubernetes/containerd.
  Requires `curl` + `jq`.

Both read overridable defaults at the top of the file (or from an optional
`/etc/homelabcmdb-*.env` file) — set `host_label`, `cmdb_container`, `cmdb_data_dir`,
etc. to match your deployment.

Both scripts support two **feed modes** via `cmdb_feed`:

- `exec` (default) — for the host that runs the CMDB container: the envelope is
  written into `cmdb_data_dir` and imported with `docker exec … cmdb import trivy`.
  `cmdb_data_dir` must then be the host path mounted to the container's `/data`,
  so the script and the container see the same file.
- `http` — for **any other host** (e.g. a pure-k8s node or the registry machine):
  the envelope is POSTed to `${cmdb_url}/import/upload/trivy` instead. Set
  `cmdb_url` (e.g. `http://cmdb.example.lan:8080`); `cmdb_data_dir` is then just a
  local spool/retention directory. Remember the endpoint is unauthenticated —
  keep the CMDB port LAN/tailnet-only.

### Run on a schedule

**systemd timer** — templates are in
[`scripts/systemd/`](../scripts/systemd). As root:

```bash
install -m 0755 scripts/trivy-scan.sh /usr/local/sbin/trivy-scan.sh
cp scripts/systemd/homelabcmdb-image-scan.{service,timer} /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now homelabcmdb-image-scan.timer   # daily at 04:00 by default
systemctl start homelabcmdb-image-scan.service        # run once, on demand
```

**cron alternative** (non-systemd hosts) — daily at 04:00:

```cron
0 4 * * *  /usr/local/sbin/trivy-scan.sh >> /var/log/homelabcmdb-image-scan.log 2>&1
```

## Rolling your own scanner

You don't have to use trivy or these scripts — anything that can emit the envelope
above will work. The minimum is: produce one `trivy image --format json`-shaped
report per image (at least `ArtifactName` and `Results[].Vulnerabilities[]`), wrap
them in `{host, scanned_at, trivy_version, images:[...]}`, and pipe the file through
`cmdb import trivy` (or the web upload). The example scripts' `jq` steps show exactly
how the envelope is assembled.
