# E2E Test Plan — Agent Assembly Line

## Test Tiers

### Tier 1: In-Process E2E (no external deps, SQLite + fakeredis)
Full pipeline with real orchestrator, real state backends, fake Redis, mocked K8s.

### Tier 2: Docker E2E (real Redis, SQLite, mocked K8s)
FastAPI TestClient hitting real endpoints, real Redis via testcontainers.

### Tier 3: K8s E2E (real Redis, real K8s, mock agent)
Deploys to local K8s, spawns real Jobs with a mock agent image.
