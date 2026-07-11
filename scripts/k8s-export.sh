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
shift
DESCRIPTION=""
CTX_FLAG=""

while [[ $# -gt 0 ]]; do
  case "$1" in
    --context) CTX_FLAG="--context ${2:?--context requires a value}"; shift 2 ;;
    *) DESCRIPTION="$1"; shift ;;
  esac
done

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

# Running pods -> workload rows (one per container) for image placements.
WORKLOADS=$(kubectl get pods -A $CTX_FLAG -o json | jq '[
  .items[]
  | select(.status.phase == "Running")
  | . as $p
  | .spec.containers[]
  | {
      namespace: $p.metadata.namespace,
      pod_name: $p.metadata.name,
      container_name: .name,
      image: .image
    }
]')

jq -n \
  --arg cluster  "$CLUSTER_NAME" \
  --arg desc     "$DESCRIPTION" \
  --argjson nodes "$NODES" \
  --argjson ns    "$NAMESPACES" \
  --argjson wl    "$WORKLOADS" \
  '{cluster: $cluster, description: $desc, nodes: $nodes, namespaces: $ns, workloads: $wl}'
