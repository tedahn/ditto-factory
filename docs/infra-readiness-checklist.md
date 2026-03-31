# Infrastructure Readiness: Real Agent Pods on kind-ditto-e2e

**Date**: 2026-03-30

## Answers

### 1. Is the real agent Dockerfile complete?
**Yes, mostly.** It installs Node 22, git, python3, build-essential, curl, jq, redis-tools, and `@anthropic-ai/claude-code` globally. It runs as non-root (UID 1000). It CAN run headless Claude Code.

**BUG FOUND**: Dockerfile copies `mcp.json` to `/etc/aal/mcp.json` but `entrypoint.sh` reads from `/etc/df/mcp.json`. This will cause MCP config to silently fail.

### 2. Entrypoint flow
1. Validates env vars: `THREAD_ID`, `REDIS_URL`, `ANTHROPIC_API_KEY`
2. Connects to Redis, reads `task:$THREAD_ID` key as JSON
3. Parses `task`, `system_prompt`, `task_type` from payload
4. Branches by task_type: `code_change` (clone, run claude, push) vs `analysis`/`file_output` (run claude, capture result.json)
5. Injects toolkit skills + gateway MCP config before running Claude
6. Runs `claude -p "$TASK" --allowedTools '*' --mcp-config "$MCP_CONFIG"`
7. Publishes result JSON back to Redis at `result:$THREAD_ID`

### 3. Namespace
Spawner defaults to `"default"` but accepts any namespace. E2E tests use `e2e-ditto-test`. Onboarding agents should use `e2e-ditto-test` since that's where `df-secrets` exists.

### 4. Secrets
`df-secrets` exists in `e2e-ditto-test` namespace with 1 data key (presumably `anthropic-api-key`). Verify the key name matches what spawner expects.

### 5. Controller-to-kind network path
Docker Compose controller does NOT mount kubeconfig or connect to the kind network. It cannot reach the kind API server out of the box. The e2e tests use `e2e-live-run.py` which loads kubeconfig directly on the host.

### 6. Minimum steps listed below.

---

## Checklist

### READY
- [x] Kind cluster `kind-ditto-e2e` is running
- [x] Namespace `e2e-ditto-test` exists
- [x] Secret `df-secrets` exists in `e2e-ditto-test` (1 key, 9d old)
- [x] Local registry `kind-registry` running on port 5001
- [x] Dockerfile installs Claude Code CLI, git, node, python
- [x] Entrypoint handles full Redis task lifecycle
- [x] Spawner creates Jobs with proper security context (non-root, drop ALL caps)
- [x] Mock agent images built and pushed to local registry

### MISSING / BROKEN
- [ ] **BUG: MCP path mismatch** -- Dockerfile: `/etc/aal/mcp.json`, entrypoint reads: `/etc/df/mcp.json`
- [ ] **Real agent image not built** -- no `agent:latest` or `localhost:5001/agent:latest` exists
- [ ] **Image not pushed to kind registry** -- kind nodes can only pull from `localhost:5001`
- [ ] **Controller cannot reach kind API** -- Docker Compose has no kubeconfig mount or kind network access
- [ ] **Verify secret key name** -- confirm `df-secrets` contains key `anthropic-api-key` (not `ANTHROPIC_API_KEY`)

---

## Fix Commands

```bash
# 1. Fix MCP path mismatch in Dockerfile (change /etc/aal to /etc/df)
sed -i '' 's|/etc/aal/mcp.json|/etc/df/mcp.json|' images/agent/Dockerfile

# 2. Build real agent image
docker build -t localhost:5001/agent:latest images/agent/

# 3. Push to kind-local registry
docker push localhost:5001/agent:latest

# 4. Load into kind (alternative to registry push)
kind load docker-image localhost:5001/agent:latest --name ditto-e2e

# 5. Verify secret key name
kubectl --context kind-ditto-e2e get secret df-secrets -n e2e-ditto-test -o jsonpath='{.data}' | python3 -c "import sys,json; print(list(json.loads(sys.stdin.read()).keys()))"

# 6. Test pod manually (replace REDIS_URL with NodePort Redis)
kubectl --context kind-ditto-e2e run agent-test \
  -n e2e-ditto-test \
  --image=localhost:5001/agent:latest \
  --env="THREAD_ID=test-001" \
  --env="REDIS_URL=redis://redis.e2e-ditto-test.svc:6379" \
  --env="ANTHROPIC_API_KEY=sk-ant-test" \
  --restart=Never \
  --rm -it

# 7. For controller access to kind API from Docker Compose, add to compose:
#   controller:
#     volumes:
#       - ${HOME}/.kube/config:/root/.kube/config:ro
#     extra_hosts:
#       - "host.docker.internal:host-gateway"
#     environment:
#       KUBECONFIG: /root/.kube/config
```
