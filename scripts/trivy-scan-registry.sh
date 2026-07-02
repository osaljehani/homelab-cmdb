#!/usr/bin/env bash
#
# trivy-scan-registry.sh — OPTIONAL variant of trivy-scan.sh for users who run an
# OCI registry with an embedded CVE/vulnerability API (e.g. Zot with its Trivy
# extension). It pulls vulnerability data for every image stored in the registry's
# catalog and feeds it to HomeLabCMDB using the SAME envelope contract as the
# Docker runtime scan (scripts/trivy-scan.sh).
#
# Use this to scan images that never run on the Docker daemon — e.g. images pulled
# through the registry by a Kubernetes/containerd mirror. It complements, and does
# not replace, the runtime Docker scan.
#
# Flow: list repos+tags via the registry's OCI catalog -> query CVEs per image via
# the registry's GraphQL search API -> transform each response into a native
# `trivy image --format json` report -> wrap all reports in the CMDB envelope ->
# run `cmdb import trivy` -> prune.
#
# Envelope contract (identical to the runtime scan — see docs/image-scanning.md):
#   { "host": ..., "scanned_at": ..., "trivy_version": ..., "images": [<trivy report>...] }
#
# The catalog returns repo paths without the host:port prefix, so ArtifactName is
# already the logical image name. Set a distinct host_label so these registry-sourced
# scans are distinguishable in CMDB from the runtime Docker scans.
#
# Requires: curl, jq. Configure by editing the defaults below or via an env file.
# NOTE: the GraphQL query shape below targets Zot's search extension
# (/v2/_zot/ext/search, CVEListForImage). Adjust it for other registries.

set -euo pipefail

ENV_FILE="${ENV_FILE:-/etc/homelabcmdb-registry-scan.env}"
# shellcheck source=/dev/null
[[ -r "$ENV_FILE" ]] && source "$ENV_FILE"

# Defaults (override via the env file above or by exporting before invocation) ---
: "${host_label:=registry}"                       # envelope "host" field
: "${zot_url:=https://registry.example.com:5000}"  # registry base URL
: "${zot_user:=admin}"
: "${zot_pass:=}"                                  # required — set in the env file
: "${zot_cacert:=/path/to/registry-ca.crt}"        # CA for the registry's TLS cert
: "${cmdb_container:=homelabcmdb-cmdb-1}"
: "${cmdb_data_dir:=./data}"
: "${cmdb_cmd:=uv run cmdb}"
: "${trivy_version:=registry-embedded}"
: "${scan_retention_days:=14}"

log() { echo "[$(date -u +%FT%TZ)] $*"; }
die() { echo "[$(date -u +%FT%TZ)] ERROR: $*" >&2; exit 1; }

command -v jq >/dev/null || die "jq not installed"
command -v curl >/dev/null || die "curl not installed"
[[ -n "$zot_pass" ]] || die "zot_pass is empty (set it in $ENV_FILE)"

# curl helper — always authenticated + CA-pinned --------------------------------
CURL=(curl -fsS --cacert "$zot_cacert" -u "${zot_user}:${zot_pass}")
GQL_ENDPOINT="${zot_url}/v2/_zot/ext/search"

gql() {  # $1 = graphql query string -> raw JSON response on stdout
  "${CURL[@]}" -H 'Content-Type: application/json' \
    -X POST "$GQL_ENDPOINT" \
    --data "$(jq -n --arg q "$1" '{query:$q}')"
}

workdir="$(mktemp -d)"
cleanup() { rm -rf "$workdir"; }
trap cleanup EXIT

scan_dir="${cmdb_data_dir}/scans"
mkdir -p "$scan_dir"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
scanned_at="$(date -u +%FT%TZ)"
out="${scan_dir}/${ts}.json"

# Enumerate repositories, then tags per repo ------------------------------------
log "listing repositories from ${zot_url}"
mapfile -t repos < <("${CURL[@]}" "${zot_url}/v2/_catalog" | jq -r '.repositories[]?' | sort -u)
[[ ${#repos[@]} -gt 0 ]] || die "no repositories in registry — nothing to scan"

reports="${workdir}/reports.ndjson"
: > "$reports"
ok=0
failed=0

for repo in "${repos[@]}"; do
  mapfile -t tags < <("${CURL[@]}" "${zot_url}/v2/${repo}/tags/list" | jq -r '.tags[]?')
  for tag in "${tags[@]}"; do
    ref="${repo}:${tag}"
    log "cve: ${ref}"

    # Grab the manifest digest (cheap HEAD) so CMDB records <repo>@sha256:... the
    # same way the runtime Docker scan does. Non-fatal if unavailable.
    digest="$(curl -fsSI --cacert "$zot_cacert" -u "${zot_user}:${zot_pass}" \
        -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.oci.image.manifest.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json' \
        "${zot_url}/v2/${repo}/manifests/${tag}" 2>/dev/null \
      | tr -d '\r' | awk 'tolower($1)=="docker-content-digest:"{print $2}')"
    repo_digest=""
    [[ -n "$digest" ]] && repo_digest="${repo}@${digest}"

    # CVEListForImage returns CVEs each with a PackageList; trivy-native wants one
    # Vulnerability per (CVE, package), so we flatten below.
    query="{ CVEListForImage(image: \"${ref}\") { CVEList { Id Title Severity PackageList { Name InstalledVersion FixedVersion } } } }"
    if ! gql "$query" > "${workdir}/one.json" 2>"${workdir}/one.err"; then
      failed=$((failed + 1))
      log "WARN: query failed for ${ref} (skipped): $(tr '\n' ' ' < "${workdir}/one.err" | tail -c 300)"
      continue
    fi

    # Transform GraphQL -> native trivy report shape expected by CMDB's importer
    # (cmdb/domain/services/trivy_import.py::_ingest_report).
    jq -c --arg ref "$ref" --arg repodigest "$repo_digest" '
      ( .data.CVEListForImage.CVEList // [] ) as $cves
      | {
          ArtifactName: $ref,
          Metadata: (if $repodigest == "" then {} else {RepoDigests: [$repodigest]} end),
          Results: [
            {
              Target: $ref,
              Vulnerabilities: (
                $cves
                | map(
                    . as $cve
                    | ( $cve.PackageList // [ {} ] )
                    | map({
                        VulnerabilityID: $cve.Id,
                        PkgName: .Name,
                        InstalledVersion: .InstalledVersion,
                        FixedVersion: .FixedVersion,
                        Severity: $cve.Severity,
                        Title: $cve.Title
                      })
                  )
                | add // []
              )
            }
          ]
        }' "${workdir}/one.json" >> "$reports"
    ok=$((ok + 1))
  done
done

[[ $ok -gt 0 ]] || die "all image queries failed — see logs"
log "queried ok=${ok} failed=${failed}"

# Wrap the per-image reports in the envelope contract ---------------------------
jq -s \
  --arg host "$host_label" \
  --arg at "$scanned_at" \
  --arg tv "$trivy_version" \
  '{host: $host, scanned_at: $at, trivy_version: $tv, images: .}' \
  "$reports" > "$out"
log "wrote ${out}"

# Feed it into CMDB (path is relative to the container's /data mount) ------------
# shellcheck disable=SC2086
docker exec "$cmdb_container" $cmdb_cmd import trivy "/data/scans/${ts}.json" \
  || die "cmdb import failed (container ${cmdb_container})"

# Prune raw envelopes; the DB retains full history ------------------------------
find "$scan_dir" -maxdepth 1 -type f -name '*.json' -mtime "+${scan_retention_days}" -delete
log "done"
