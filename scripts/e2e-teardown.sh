#!/bin/bash
# scripts/e2e-teardown.sh
# Destroy the kind cluster and local registry used for E2E tests.
set -euo pipefail

CLUSTER_NAME="ditto-e2e"
REGISTRY_NAME="kind-registry"

echo "=== Deleting kind cluster ==="
kind delete cluster --name "$CLUSTER_NAME" 2>/dev/null || true

echo "=== Stopping local registry ==="
docker rm -f "$REGISTRY_NAME" 2>/dev/null || true

echo "=== E2E environment torn down ==="
