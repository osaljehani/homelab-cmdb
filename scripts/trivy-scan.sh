#!/usr/bin/env bash
#
# trivy-scan.sh — scan a host's running Docker images with a pinned, ephemeral
# trivy container and feed the vulnerability reports to HomeLabCMDB.
#
# Nothing trivy is installed on the host; the pinned aquasec/trivy image is the
# only dependency, and a named cache volume persists trivy's vuln DB between runs.
#
# Flow: enumerate running images -> trivy scan each -> wrap the reports in the
# CMDB envelope contract -> feed to CMDB -> prune old envelopes.
#
# Two feed modes (cmdb_feed):
#   exec (default) — write into CMDB's ./data/scans volume and run
#                    `cmdb import trivy` inside the local CMDB container.
#                    Requires this host to run the CMDB container.
#   http           — POST the envelope to a (remote) CMDB's upload endpoint
#                    (/import/upload/trivy). Use on hosts that do NOT run CMDB;
#                    cmdb_data_dir is then just a local spool/retention dir.
#
# Envelope contract (the only coupling point with CMDB — see docs/image-scanning.md):
#   { "host": ..., "scanned_at": ..., "trivy_version": ..., "images": [<trivy report>...] }
#
# Requires: docker, jq. Run on each Docker host (typically via a systemd timer or
# cron — see scripts/systemd/ and docs/image-scanning.md). Configure by editing the
# defaults below or by exporting the same variables from an optional env file.

set -euo pipefail

# Optional env file for overrides (KEY=value lines). Safe to leave absent.
ENV_FILE="${ENV_FILE:-/etc/homelabcmdb-image-scan.env}"
# shellcheck source=/dev/null
[[ -r "$ENV_FILE" ]] && source "$ENV_FILE"

# Defaults (override via the env file above or by exporting before invocation) ---
: "${host_label:=$(hostname)}"          # goes in the envelope's "host" field
: "${cmdb_container:=homelabcmdb-cmdb-1}"  # name of the running CMDB container
: "${cmdb_data_dir:=./data}"            # host path mounted to the container's /data
: "${cmdb_cmd:=uv run cmdb}"            # how to invoke the CLI inside the container
: "${cmdb_feed:=exec}"                  # exec | http (see feed modes above)
: "${cmdb_url:=}"                       # CMDB base URL for cmdb_feed=http, e.g. http://cmdb.example.lan:8080
: "${trivy_version:=0.72.0}"
: "${trivy_image:=aquasec/trivy:0.72.0}"
: "${trivy_cache_vol:=trivy-cache}"
: "${scan_retention_days:=14}"

log() { echo "[$(date -u +%FT%TZ)] $*"; }
die() { echo "[$(date -u +%FT%TZ)] ERROR: $*" >&2; exit 1; }

command -v docker >/dev/null || die "docker not installed"
command -v jq >/dev/null || die "jq not installed"

# Validate the feed mode up front so a misconfiguration fails before scanning ----
case "$cmdb_feed" in
  exec) ;;
  http)
    command -v curl >/dev/null || die "curl required for cmdb_feed=http"
    [[ -n "$cmdb_url" ]] || die "cmdb_url is empty (e.g. http://cmdb.example.lan:8080)"
    ;;
  *) die "unknown cmdb_feed '${cmdb_feed}' (exec|http)" ;;
esac

# Temp workspace for the per-image trivy reports (one JSON object per file) ------
workdir="$(mktemp -d)"
cleanup() { rm -rf "$workdir"; }
trap cleanup EXIT

scan_dir="${cmdb_data_dir}/scans"
mkdir -p "$scan_dir"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
scanned_at="$(date -u +%FT%TZ)"
out="${scan_dir}/${ts}.json"

# Enumerate the images backing running containers (deduped) ---------------------
mapfile -t images < <(docker ps --format '{{.Image}}' | sort -u)
[[ ${#images[@]} -gt 0 ]] || die "no running containers found — nothing to scan"
log "scanning ${#images[@]} image(s) with ${trivy_image}"

reports="${workdir}/reports.ndjson"
: > "$reports"
ok=0
failed=0
for ref in "${images[@]}"; do
  log "trivy: ${ref}"
  if docker run --rm \
        -v /var/run/docker.sock:/var/run/docker.sock \
        -v "${trivy_cache_vol}:/root/.cache/trivy" \
        "$trivy_image" image --quiet --format json --scanners vuln "$ref" \
        > "${workdir}/one.json" 2>"${workdir}/one.err"; then
    cat "${workdir}/one.json" >> "$reports"
    ok=$((ok + 1))
  else
    failed=$((failed + 1))
    log "WARN: trivy failed for ${ref} (skipped): $(tr '\n' ' ' < "${workdir}/one.err" | tail -c 300)"
  fi
done

# If every scan failed, exit non-zero so a systemd OnFailure/cron alert can fire.
[[ $ok -gt 0 ]] || die "all ${#images[@]} image scan(s) failed — see logs"
log "scanned ok=${ok} failed=${failed}"

# Wrap the per-image trivy reports in the envelope contract via jq ---------------
jq -s \
  --arg host "$host_label" \
  --arg at "$scanned_at" \
  --arg tv "$trivy_version" \
  '{host: $host, scanned_at: $at, trivy_version: $tv, images: .}' \
  "$reports" > "$out"
log "wrote ${out}"

# Feed it into CMDB -------------------------------------------------------------
case "$cmdb_feed" in
  exec)
    # The path is relative to the container's /data mount.
    # $cmdb_cmd is deliberately word-split (e.g. `uv run cmdb`).
    # shellcheck disable=SC2086
    docker exec "$cmdb_container" $cmdb_cmd import trivy "/data/scans/${ts}.json" \
      || die "cmdb import failed (container ${cmdb_container})"
    ;;
  http)
    # The upload endpoint is unauthenticated — keep the CMDB port LAN/tailnet-only.
    curl -fsS -F "files=@${out}" "${cmdb_url%/}/import/upload/trivy" >/dev/null \
      || die "cmdb http import failed (${cmdb_url})"
    ;;
esac

# Prune raw envelopes; the DB retains the full scan history ----------------------
find "$scan_dir" -maxdepth 1 -type f -name '*.json' -mtime "+${scan_retention_days}" -delete
log "done"
