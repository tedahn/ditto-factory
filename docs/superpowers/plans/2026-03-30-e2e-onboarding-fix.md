# E2E Agent-Driven Onboarding — What's Needed

## Current State

| Component | Status | Details |
|-----------|--------|---------|
| Kind cluster | RUNNING | `ditto-e2e`, node Ready, K8s v1.35.0 |
| kubectl context | SET | `kind-ditto-e2e` |
| Local registry | RUNNING | `localhost:5001` |
| Agent image | MOCK ONLY | `localhost:5001/mock-agent:latest` — this is a test mock, NOT a real Claude Code agent |
| Real agent image | NOT BUILT | No `ditto-factory-agent` image exists |
| K8s secrets | MISSING | No `df-secrets` secret (needed for ANTHROPIC_API_KEY) |
| Controller → K8s | NOT WIRED | Controller runs in Docker Compose, not connected to kind cluster |
| Redis (K8s) | RUNNING | In `e2e-ditto-test` namespace, port 16379 on host |
| Redis (Docker) | RUNNING | In docker-compose, port 6379 |
| Result path | EXISTS | `orchestrator.py:523` calls `workflow_engine.handle_agent_result()` on job completion |
| Workflow template | REGISTERED | `toolkit-onboarding` template in DB |
| /onboard endpoint | SHORTCUT | Calls discovery.discover() directly, skips workflow engine |

## Gaps to Fix (in order)

### 1. Build real agent image with Claude Code
The mock-agent is a bash script that simulates agent behavior. We need the real agent image that:
- Has Claude Code CLI installed
- Has git, node, python
- Reads task from Redis (`THREAD_ID`)
- Runs Claude Code headlessly
- Reports results back to Redis

**File:** `images/agent/Dockerfile` — already exists but needs to be built and pushed to kind registry:
```bash
docker build -t localhost:5001/ditto-factory-agent:latest images/agent/
docker push localhost:5001/ditto-factory-agent:latest
```

### 2. Create K8s secrets
Agent pods need ANTHROPIC_API_KEY to run Claude Code:
```bash
kubectl --context kind-ditto-e2e create secret generic df-secrets \
  --from-literal=anthropic-api-key=$ANTHROPIC_API_KEY \
  -n e2e-ditto-test
```

### 3. Wire controller to kind cluster
The controller in Docker Compose needs to reach the kind K8s API. Options:
- **Option A:** Run controller OUTSIDE Docker, directly on host (simplest for dev — just `python -m uvicorn`)
- **Option B:** Mount kubeconfig into the controller container + set KUBECONFIG env var
- **Option C:** Deploy controller INTO the kind cluster (full K8s deployment)

**Recommendation:** Option A for now — run controller on host. It can reach both Redis (localhost:6379) and K8s API (from kubeconfig).

### 4. Configure controller settings for kind
```env
DF_AGENT_IMAGE=localhost:5001/ditto-factory-agent:latest
DF_REDIS_URL=redis://localhost:6379  # or redis://localhost:16379 for K8s redis
```

### 5. Wire /onboard to workflow engine
**File:** `controller/src/controller/toolkits/api.py`
Change the `start_onboarding` endpoint to:
1. Call `workflow_engine.start("toolkit-onboarding", parameters={"github_url": url, "branch": branch}, thread_id=thread_id)`
2. Return the execution_id
3. Frontend polls `/onboard/{execution_id}` for status

### 6. Handle workflow completion → toolkit import
When the agent step completes, the workflow engine needs to:
1. Get the structured manifest JSON from the agent's output
2. Call `validate_and_import_manifest()` from the onboarding template
3. Store the result

This can be done in `handle_agent_result` or as a callback.

## Minimum Viable E2E

The fastest path to a working E2E:
1. Build + push real agent image to kind registry
2. Create K8s secret with Anthropic API key
3. Run controller on host (not in Docker)
4. Wire /onboard → workflow_engine.start()
5. Test: POST /onboard → agent pod spawns → analyzes repo → returns manifest → toolkit imported

## What We Can Skip For Now
- Running controller in Docker (host is fine for dev)
- ConfigMap file mounting (agent can work without it for onboarding)
- The loadout system (onboarding agent just needs git + Claude Code)
