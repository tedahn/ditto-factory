#!/bin/bash
# scripts/e2e-setup.sh
# One-shot setup for local E2E test environment.
set -euo pipefail

CLUSTER_NAME="ditto-e2e"
REGISTRY_NAME="kind-registry"
REGISTRY_PORT=5001
NAMESPACE="e2e-ditto-test"

echo "=== Creating kind cluster ==="
kind create cluster --name "$CLUSTER_NAME" --config - <<'EOF'
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
nodes:
  - role: control-plane
    extraPortMappings:
      - containerPort: 30379
        hostPort: 16379
        protocol: TCP
EOF

echo "=== Starting local registry ==="
docker run -d --restart=always -p "${REGISTRY_PORT}:5000" \
  --name "$REGISTRY_NAME" --network kind registry:2 2>/dev/null || true

echo "=== Building mock agent images ==="
docker build -t "localhost:${REGISTRY_PORT}/mock-agent:latest" images/mock-agent/
docker push "localhost:${REGISTRY_PORT}/mock-agent:latest"

# Build variant images for error tests
for variant in fail-clone zero-commits slow; do
    case "$variant" in
        fail-clone)   BUILD_ARGS="--build-arg MOCK_FAIL_PHASE=clone" ;;
        zero-commits) BUILD_ARGS="--build-arg MOCK_COMMIT_COUNT=0" ;;
        slow)         BUILD_ARGS="--build-arg MOCK_DELAY_SECONDS=300" ;;
    esac
    docker build -t "localhost:${REGISTRY_PORT}/mock-agent:${variant}" \
        $BUILD_ARGS images/mock-agent/
    docker push "localhost:${REGISTRY_PORT}/mock-agent:${variant}"
done

echo "=== Deploying test infrastructure ==="
kubectl create namespace "$NAMESPACE" 2>/dev/null || true
kubectl apply -f controller/tests/e2e_k8s/manifests/

echo "=== Waiting for Redis to be ready ==="
kubectl wait --for=condition=available deployment/redis \
  -n "$NAMESPACE" --timeout=120s

echo "=== Verifying Redis connectivity ==="
# Test host-side connectivity via NodePort
for i in $(seq 1 10); do
    if redis-cli -u redis://localhost:16379 PING 2>/dev/null | grep -q PONG; then
        echo "Redis is reachable at localhost:16379"
        break
    fi
    echo "Waiting for Redis NodePort... ($i/10)"
    sleep 3
done

echo ""
echo "=== E2E environment ready ==="
echo "Run tests with:"
echo "  DF_E2E_K8S=1 uv run pytest controller/tests/e2e_k8s/ -v --timeout=180"
