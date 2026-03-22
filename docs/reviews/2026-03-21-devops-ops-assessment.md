# Operational Assessment: Traceability Approaches for Ditto Factory

**Date**: 2026-03-21
**Author**: DevOps Automator
**Context**: Ditto Factory — Python/FastAPI on K8s, ~50 runs/day scaling to 1000/day

## Current Infrastructure Baseline

| Component       | Status      | Notes                                          |
|-----------------|-------------|-------------------------------------------------|
| Kubernetes      | Running     | Helm chart (charts/ditto-factory/), agent Jobs  |
| Redis           | Running     | Communication bus, state, streams               |
| SQLite          | Running     | Controller-side state, performance tracking     |
| PostgreSQL      | Running     | Primary persistent storage                      |
| Docker Images   | 4 images    | controller, gateway, agent, mock-agent          |
| Monitoring      | Minimal     | Basic structured logging only                   |

---

## Approach A: OpenTelemetry-Native

### Deployment Complexity: HIGH

New services required:
- OTel Collector (DaemonSet or sidecar per pod) — 1 new DaemonSet
- Trace backend (Jaeger or Grafana Tempo) — 1-2 new Deployments
- Persistent storage for traces — PVC or object storage (S3/GCS)
- Optional: Grafana for visualization — 1 new Deployment

Total new K8s manifests: **4-6 new Deployments/DaemonSets**

### Day-2 Operations

| Task                    | Frequency    | Effort       |
|-------------------------|-------------|--------------|
| OTel Collector upgrades | Quarterly   | 2-4 hours    |
| Backend upgrades        | Quarterly   | 2-4 hours    |
| Storage management      | Monthly     | 1-2 hours    |
| Sampling rule tuning    | As needed   | 1-2 hours    |
| Config drift debugging  | Ad hoc      | 2-8 hours    |

Estimated ongoing: **4-6 hours/month**

### Resource Requirements

| Component        | CPU     | Memory   | Disk          |
|------------------|---------|----------|---------------|
| OTel Collector   | 100m    | 256Mi    | —             |
| Jaeger/Tempo     | 200m    | 512Mi    | 10-50GB/month |
| Grafana          | 100m    | 256Mi    | 1GB           |
| **Total**        | **400m**| **1Gi**  | **11-51GB**   |

At 1000 runs/day (assuming ~50 spans/run, 1KB/span):
- 50,000 spans/day = ~50MB/day = **1.5GB/month** raw traces
- With 30-day retention: manageable; 90-day: needs object storage tier

### Failure Blast Radius: LOW-MEDIUM

- OTel SDK uses async exporters — if collector is down, spans are dropped silently
- Agent execution is unaffected
- Risk: if collector sidecar is injected and fails to start, it can block pod startup (sidecar mode)
- DaemonSet mode avoids this risk

### Security Surface

- Collector exposes gRPC/HTTP receivers (ports 4317/4318) — cluster-internal only
- Trace data may contain task descriptions, code snippets, LLM prompts
- Needs NetworkPolicy to restrict collector ingress
- No new external credentials required (unless using cloud-hosted backend)

### Cost

| Scale       | Infra Cost/month | Notes                              |
|-------------|------------------|------------------------------------|
| 50 runs/day | $15-30           | Minimal resources, local storage   |
| 1000/day    | $50-150          | Larger storage, possible S3 tier   |
| Cloud-hosted| $100-500         | Grafana Cloud / Datadog pricing    |

---

## Approach B: Lightweight Structured Logs + SQLite

### Deployment Complexity: NONE

New services required: **zero**
- Python `logging` module — already in use
- SQLite — already in the stack for performance tracking
- JSON structured logging — configuration change only

Total new K8s manifests: **0**

### Day-2 Operations

| Task                    | Frequency    | Effort       |
|-------------------------|-------------|--------------|
| Log rotation config     | Once        | 30 min       |
| SQLite vacuum/compact   | Monthly     | 15 min       |
| Schema migrations       | Rare        | 30 min       |
| Storage monitoring      | Monthly     | 15 min       |

Estimated ongoing: **1-2 hours/month**

### Resource Requirements

| Component        | CPU     | Memory   | Disk          |
|------------------|---------|----------|---------------|
| Logging overhead | ~0      | ~10Mi    | —             |
| SQLite file      | ~0      | ~20Mi    | 50-500MB      |
| **Total**        | **~0**  | **30Mi** | **50-500MB**  |

At 1000 runs/day:
- ~1KB per trace record x 1000 = 1MB/day = **30MB/month**
- With metadata and indexes: ~100MB/month
- SQLite handles millions of rows well at this scale

### Failure Blast Radius: VERY LOW

- Logging is synchronous and local — almost impossible to fail independently
- SQLite write failure would surface as a caught exception; agent execution continues
- No network dependency for tracing
- Worst case: disk full on controller pod — affects controller broadly, not just tracing

### Security Surface

- No new network endpoints
- No new credentials
- Trace data stays local to controller pod
- SQLite file inherits pod filesystem permissions
- Risk: SQLite file on ephemeral storage is lost on pod restart (needs PVC or periodic backup)

### Cost

| Scale       | Infra Cost/month | Notes                        |
|-------------|------------------|------------------------------|
| 50 runs/day | $0               | Uses existing resources      |
| 1000/day    | $0-5             | Marginal disk increase       |

---

## Approach C: Event Sourcing with Redis Streams

### Deployment Complexity: LOW-MEDIUM

New services required:
- Materializer worker — 1 new Deployment or CronJob
- Redis Streams — already available (feature of existing Redis)

Total new K8s manifests: **1 new Deployment**

### Day-2 Operations

| Task                         | Frequency    | Effort       |
|------------------------------|-------------|--------------|
| Stream trimming/retention    | Weekly      | 30 min       |
| Materializer monitoring      | Weekly      | 30 min       |
| Redis memory management      | Monthly     | 1-2 hours    |
| Consumer group lag monitoring | Weekly      | 30 min       |
| Worker crash recovery        | Ad hoc      | 1-2 hours    |

Estimated ongoing: **3-5 hours/month**

### Resource Requirements

| Component          | CPU     | Memory   | Disk          |
|--------------------|---------|----------|---------------|
| Redis Streams      | shared  | 50-200Mi | —             |
| Materializer       | 50m     | 128Mi    | —             |
| Read model storage | ~0      | —        | 100MB-1GB    |
| **Total**          | **50m** | **200-330Mi** | **100MB-1GB** |

At 1000 runs/day:
- Each event ~500 bytes x 50 events/run = 25KB/run
- 25MB/day in Redis memory (before trimming)
- With MAXLEN trimming at 100K entries: ~50MB Redis memory ceiling
- Critical: Redis memory is expensive — this competes with existing Redis workload

### Failure Blast Radius: MEDIUM-HIGH

- Redis is shared with the core communication bus
- Stream memory pressure can cause Redis OOM, taking down agent communication
- Materializer backlog can cause unbounded stream growth
- If materializer crashes and is not restarted, streams grow until Redis memory limit
- **This is the highest-risk approach** because failure directly impacts the critical path

### Security Surface

- No new network endpoints (uses existing Redis connection)
- Stream data visible to anything with Redis access
- No new credentials
- Risk: no built-in access control within Redis — any client can read all streams

### Cost

| Scale       | Infra Cost/month | Notes                              |
|-------------|------------------|------------------------------------|
| 50 runs/day | $5-10            | Marginal Redis memory + worker     |
| 1000/day    | $20-50           | Redis memory increase, larger worker|

---

## Approach D: Langfuse-Integrated

### Deployment Complexity: VERY HIGH

New services required:
- Langfuse web application — 1 Deployment
- ClickHouse — 1 StatefulSet (analytics engine, not optional for self-hosted)
- PostgreSQL — shared or dedicated instance
- Redis — shared or dedicated instance
- Langfuse worker — 1 Deployment (background jobs)

Total new K8s manifests: **3-5 new Deployments/StatefulSets**

Langfuse self-hosted stack is essentially a second application platform.

### Day-2 Operations

| Task                         | Frequency    | Effort       |
|------------------------------|-------------|--------------|
| Langfuse version upgrades    | Monthly     | 2-4 hours    |
| ClickHouse maintenance       | Monthly     | 2-4 hours    |
| Database migrations          | Per upgrade  | 1-2 hours    |
| Backup management            | Weekly      | 1 hour       |
| SSL/auth config              | Quarterly   | 1-2 hours    |
| Storage cleanup/compaction   | Monthly     | 1 hour       |
| Debugging Langfuse issues    | Ad hoc      | 2-8 hours    |

Estimated ongoing: **8-15 hours/month**

### Resource Requirements

| Component        | CPU     | Memory   | Disk          |
|------------------|---------|----------|---------------|
| Langfuse web     | 200m    | 512Mi    | —             |
| Langfuse worker  | 100m    | 256Mi    | —             |
| ClickHouse       | 500m    | 1Gi      | 20-100GB      |
| PostgreSQL (if dedicated) | 200m | 512Mi | 5-20GB    |
| **Total**        | **1000m**| **2.3Gi**| **25-120GB** |

At 1000 runs/day:
- ClickHouse is efficient for analytics but has a high base resource cost
- Storage grows linearly; ClickHouse compression helps (~5x)
- Realistic: 10-20GB/month at scale

### Failure Blast Radius: LOW

- Langfuse SDK is fire-and-forget — agent execution is unaffected
- If Langfuse is down, traces are lost but operations continue
- Risk: if sharing PostgreSQL/Redis with Ditto Factory, resource contention is possible
- Dedicated instances eliminate this but double the resource footprint

### Security Surface

- Langfuse web UI exposed as a Service (needs Ingress + auth)
- New credentials: Langfuse API keys, ClickHouse credentials, database credentials
- Web UI is an attack surface if exposed externally
- Trace data contains prompts, completions, potentially sensitive code
- Needs: NetworkPolicy, TLS, authentication configuration

### Cost

| Scale       | Infra Cost/month | Notes                                   |
|-------------|------------------|-----------------------------------------|
| 50 runs/day | $40-80           | ClickHouse base cost dominates          |
| 1000/day    | $80-200          | Storage growth, higher resource requests|
| Cloud-hosted| $59-399          | Langfuse Cloud pricing tiers            |

---

## Comparison Matrix

| Criterion              | A: OTel        | B: Logs+SQLite   | C: Redis Streams | D: Langfuse     |
|------------------------|----------------|------------------|------------------|-----------------|
| Deployment Complexity  | High (4-6 svcs)| None (0 svcs)    | Low (1 svc)      | Very High (3-5) |
| Day-2 Ops (hrs/month)  | 4-6            | 1-2              | 3-5              | 8-15            |
| Resource Overhead       | 400m/1Gi       | ~0/30Mi          | 50m/330Mi        | 1000m/2.3Gi     |
| Failure Blast Radius   | Low-Med        | Very Low         | **Med-High**     | Low             |
| Security Surface       | Medium         | Very Low         | Low              | High            |
| Cost @ 50/day          | $15-30         | **$0**           | $5-10            | $40-80          |
| Cost @ 1000/day        | $50-150        | **$0-5**         | $20-50           | $80-200         |
| Team Burden (% FTE)    | 5-8%           | **1-2%**         | 4-6%             | 10-20%          |
| Query/Visualization    | Excellent      | Basic            | Custom only      | Excellent       |
| LLM-specific features  | None           | None             | None             | Built-in        |

---

## Ranking by Operational Simplicity

### 1st: Approach B — Structured Logs + SQLite

- Zero new infrastructure
- Zero new failure modes
- Fits within 1-2% DevOps time budget
- Perfectly adequate for 50-1000 runs/day
- Limitation: no fancy trace visualization, but structured JSON logs + SQLite queries cover 90% of debugging needs
- Upgrade path: can add OTel later when team/scale justifies it

### 2nd: Approach C — Redis Streams (with caveats)

- Leverages existing Redis, only 1 new worker
- Moderate ops burden (3-5 hrs/month)
- **Serious risk**: shares memory with critical Redis bus; a materializer outage can cascade
- Only recommended if Redis is oversized and has clear memory headroom
- Mitigation: MAXLEN trimming, memory alerts, dedicated Redis instance (but then cost/complexity rises)

### 3rd: Approach A — OpenTelemetry

- Industry standard, excellent tooling
- But 4-6 new services for a small team is significant
- 5-8% DevOps time is borderline for your constraint
- Best suited when you already have a Grafana/Prometheus stack
- Recommended as the Phase 2 upgrade from Approach B

### 4th: Approach D — Langfuse

- Best traceability features for LLM workloads
- But self-hosted Langfuse is essentially running a second product
- 10-20% DevOps time exceeds your 10% budget on its own
- ClickHouse is a heavy dependency for a small team
- Cloud-hosted Langfuse ($59-399/mo) is viable but adds vendor dependency
- Recommended only if LLM-specific observability (prompt versioning, eval tracking) is a hard requirement

---

## Recommendation

**Deploy Approach B (Structured Logs + SQLite) now.** It costs nothing, adds no operational burden, and provides sufficient traceability for your current scale and team size.

Design the logging schema to be forward-compatible with OpenTelemetry semantic conventions (trace_id, span_id, service.name). This makes a future migration to Approach A straightforward — you swap the logging backend for an OTel exporter without changing application code.

**Upgrade trigger**: When you hit 500+ runs/day AND have a dedicated DevOps person (not 10% of someone's time), evaluate Approach A with Grafana Tempo as the backend.

**Avoid Approach C** unless you split Redis into separate instances (tracing vs. communication), which negates the "already in the stack" advantage.

**Consider Langfuse Cloud** (not self-hosted) only if your team specifically needs LLM evaluation tracking, prompt versioning, or cost-per-token dashboards that justify the subscription cost.
