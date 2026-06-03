#!/usr/bin/env bash
# Generate a CMDB K8s import JSON for a cluster.
# Requires: kubectl, jq
#
# Usage:
#   ./scripts/k8s-export.sh <cluster-name> [description] [--context <kubectl-context>]
#
# Output: JSON to stdout. Redirect to a file for import:
#   ./scripts/k8s-export.sh my-cluster "Production k3s" > data/my-cluster.json
#   cmdb import k8s data/my-cluster.json

set -euo pipefail

CLUSTER_NAME="${1:?Usage: k8s-export.sh <cluster-name> [description] [--context <ctx>]}"
DESCRIPTION="${2:-}"
CTX_FLAG=""

if [[ "${3:-}" == "--context" ]]; then
  CTX_FLAG="--context ${4:?--context requires a value}"
fi

# Detect node roles from standard k8s labels.
# control-plane: node-role.kubernetes.io/control-plane (>=1.20) or /master (legacy)
# etcd:          node-role.kubernetes.io/etcd
# worker:        anything else
NODES=$(kubectl get nodes $CTX_FLAG -o json | jq '[
  .items[] | {
    hostname: .metadata.name,
    role: (
      if (
        .metadata.labels["node-role.kubernetes.io/control-plane"] != null or
        .metadata.labels["node-role.kubernetes.io/master"] != null
      ) then "control-plane"
      elif .metadata.labels["node-role.kubernetes.io/etcd"] != null then "etcd"
      else "worker"
      end
    )
  }
]')

NAMESPACES=$(kubectl get namespaces $CTX_FLAG -o json | jq '[.items[].metadata.name]')

jq -n \
  --arg cluster  "$CLUSTER_NAME" \
  --arg desc     "$DESCRIPTION" \
  --argjson nodes "$NODES" \
  --argjson ns    "$NAMESPACES" \
  '{cluster: $cluster, description: $desc, nodes: $nodes, namespaces: $ns}'
