#!/usr/bin/env bash
# Generate a CMDB Docker import JSON for a host.
# Requires: docker, jq
#
# Usage:
#   ./scripts/docker-export.sh [hostname]
#
# Defaults hostname to $(hostname). Output: JSON to stdout. Redirect to a file:
#   ./scripts/docker-export.sh > data/$(hostname)-docker.json
#   cmdb import docker data/$(hostname)-docker.json
#
# Run this on each Docker host (e.g. over SSH) and import the resulting file.

set -euo pipefail

HOSTNAME_ARG="${1:-$(hostname)}"

# `docker ps --format '{{json .}}'` emits one JSON object per line.
CONTAINERS=$(docker ps --all --no-trunc --format '{{json .}}' | jq -s '[
  .[] | {
    name:            .Names,
    image:           .Image,
    status:          .Status,
    state:           .State,
    ports:           .Ports,
    compose_project: (.Labels | capture("com.docker.compose.project=(?<p>[^,]+)").p // null)
  }
]')

jq -n \
  --arg host        "$HOSTNAME_ARG" \
  --argjson conts   "$CONTAINERS" \
  '{host: $host, containers: $conts}'
