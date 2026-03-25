# Q1: Where Should LLM Intent Classification Run?

## Status
Proposed -- 2026-03-25

## Problem

Ditto Factory receives webhooks from Slack, GitHub, and Linear, and needs to classify user requests into structured intents before compiling and executing workflows. The question is where this LLM-powered classification step belongs in the request lifecycle.

Key constraints:
- **Slack webhooks must be acknowledged within 3 seconds** or they retry/fail
- LLM classification takes 1-3 seconds (variable, network-dependent)
- The system must remain functional if the LLM provider is temporarily unavailable
- Burst traffic (e.g., a CI pipeline triggering 100 webhooks) must not cause cascading failures

## Options Evaluated

### Option A: Inline in the Controller

```
Webhook -> Controller -> LLM classify (1-3s) -> Compile workflow -> Execute
```

| Criterion | Assessment |
|-----------|------------|
| Webhook timeout | FAIL -- 1-3s LLM call + processing easily exceeds Slack's 3s limit |
| User experience | Poor -- user waits for classification before getting any acknowledgment |
| Failure modes | Controller blocks on LLM downtime; webhook retries compound the problem |
| Scalability | Poor -- each request holds a controller thread during LLM call |
| Testability | Good -- simple linear flow, easy to mock LLM responses |
| Complexity | Low -- no new infrastructure |

**Verdict**: Disqualified by the Slack 3s constraint alone. Even without Slack, blocking the controller on an external LLM call is architecturally fragile.

### Option B: Async Pre-processing Step

```
Webhook -> Controller (ack immediately) -> Queue -> Worker calls LLM -> Publishes intent -> Controller executes
```

| Criterion | Assessment |
|-----------|------------|
| Webhook timeout | PASS -- controller acks in <200ms, classification happens async |
| User experience | Good -- user gets immediate "received, processing" response |
| Failure modes | Manageable -- queue absorbs bursts; dead-letter queue catches LLM failures; workers retry independently |
| Scalability | Good -- scale workers horizontally; backpressure via queue depth |
| Testability | Good -- worker is a pure function (request in, intent out); queue behavior testable separately |
| Complexity | Medium -- requires a message queue (Redis Streams, NATS, or K8s-native like CloudEvents) |

**Verdict**: Strong fit. The added complexity is justified by the constraints.

### Option C: Edge Classification

```
Slack Bot -> LLM classify -> Structured intent -> Controller -> Execute
GitHub App -> LLM classify -> Structured intent -> Controller -> Execute
```

| Criterion | Assessment |
|-----------|------------|
| Webhook timeout | Depends -- Slack bot can use response_url for deferred replies, but classification still blocks the bot |
| User experience | Potentially fastest ack if bot responds before classifying |
| Failure modes | Each integration handles LLM failure differently -- inconsistent degradation |
| Scalability | Poor -- classification logic duplicated per integration, no shared backpressure |
| Testability | Poor -- must test classification in each integration separately |
| Complexity | High -- N integrations x classification logic = maintenance burden |

**Verdict**: Violates DRY and creates a versioning nightmare. The only advantage (proximity to user) can be achieved in Option B with a fast ack pattern.

## Recommendation: Option B -- Async Pre-processing

### Architecture

```
                                    +------------------+
  Slack ----+                       |  Classification  |
            |    +-----------+      |  Worker Pool     |      +------------+
  GitHub ---+--->| Controller|--Q-->|  (N replicas)    |--Q-->| Workflow   |
            |    | (fast ack)|      |  LLM call here   |      | Executor   |
  Linear ---+    +-----------+      +------------------+      +------------+
                   <200ms ack         1-3s (async)              async
```

### Key Design Decisions

1. **Queue technology**: Use Redis Streams or NATS JetStream. Both are lightweight, support consumer groups for horizontal scaling, and are already common in K8s deployments. Avoid Kafka -- it is overkill for this throughput profile.

2. **Worker design**: Classification workers should be stateless pods with:
   - Configurable concurrency (start with 5 concurrent LLM calls per replica)
   - Circuit breaker on LLM provider (trip after 3 consecutive failures, half-open after 30s)
   - Fallback to a rule-based classifier for common intents when LLM is unavailable
   - Structured output validation (reject malformed intents back to dead-letter queue)

3. **Fast ack pattern**: Controller responds to webhook with a tracking ID immediately. For Slack, post a threaded message like "Got it, figuring out what to do..." then update the thread when classification completes.

4. **Observability**: Emit metrics on classification latency (p50/p95/p99), queue depth, circuit breaker state, and fallback-classifier usage rate. Alert when fallback rate exceeds 10%.

### Graceful Degradation Strategy

```
LLM available     -> Full classification (structured intent with confidence score)
LLM degraded      -> Circuit breaker trips -> Rule-based fallback for top-10 intents
LLM down + unknown intent -> Queue with exponential backoff, notify user of delay
```

## Trade-offs Accepted

| We gain | We give up |
|---------|-----------|
| Webhook reliability (never miss a 3s deadline) | Added infrastructure (queue + workers) |
| Horizontal scalability of classification | Slightly more complex local dev setup |
| Independent deployability of classifier | Eventual consistency (brief window between ack and intent resolution) |
| LLM provider isolation from controller | Request tracing across async boundary requires correlation IDs |

## Trade-offs Rejected

- **"Just use a faster LLM"**: Latency is inherently variable and provider-dependent. Architectural resilience beats optimistic latency assumptions.
- **"Classify in the controller with a timeout"**: A 2s timeout on classification means 2s of wasted controller capacity per request, and partial failures are harder to retry than queued work.

## Migration Path

1. **Phase 1**: Add Redis Streams + single classification worker. Controller publishes to queue, worker classifies, publishes intent event.
2. **Phase 2**: Add circuit breaker and rule-based fallback. Monitor fallback rate.
3. **Phase 3**: Auto-scale worker pool based on queue depth (KEDA scaler on Redis stream length).
