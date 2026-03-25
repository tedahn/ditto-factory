# Q4: Result Quality Signals for Data Sourcing Tasks

## Status
Proposed

## Problem

Code tasks have natural quality signals (tests pass, CI green, PR approved). Data sourcing tasks -- "find events in Dallas" -- have no equivalent. Schema-valid JSON can contain stale dates, wrong locations, fabricated URLs. We need a quality signal strategy that works at MVP and scales toward ground truth.

## Options Evaluated

### Option A: Schema validation only
- **Pro**: Fast, deterministic, zero marginal cost
- **Con**: Necessary but wildly insufficient. `{"name": "TBD", "date": "2020-01-01", "location": ""}` passes schema validation
- **Verdict**: Table stakes, not a strategy

### Option B: Schema + automated quality checks
- **Pro**: Catches the most common failure modes (empty fields, past dates, dead URLs) without LLM cost
- **Con**: Cannot assess semantic accuracy ("is this actually a tech meetup or a church potluck?")
- **Verdict**: Strong MVP foundation

### Option C: Schema + quality checks + LLM evaluation
- **Pro**: Best automated signal for semantic quality
- **Con**: $0.01-0.05 per eval adds up at fan-out scale (N agents x M results). LLM-evaluating-LLM introduces correlated bias -- if the sourcing agent hallucinated a plausible event, the evaluator may not catch it
- **Verdict**: Valuable but not for every result set

### Option D: Schema + quality checks + user feedback
- **Pro**: Builds ground truth. User is the only one who truly knows "did I get what I wanted?"
- **Con**: Slow loop. Users ignore feedback prompts if asked too often. Cannot block delivery on feedback
- **Verdict**: Essential for learning, wrong for gating

### Option E: Tiered approach
- **Pro**: Matches quality investment to task value. Immediate automated checks, async LLM eval for high-value flows, deferred user feedback for ground truth
- **Con**: More implementation complexity
- **Verdict**: This is the right answer

## Recommendation: Option E (Tiered), with a specific phasing plan

### Why E wins

The core insight is that quality signals serve different purposes at different timescales:

| Timescale | Purpose | Signal |
|-----------|---------|--------|
| Immediate (ms) | Gate bad results from reaching user | Schema + automated checks |
| Near-term (seconds) | Enrich results with confidence score | LLM eval (selective) |
| Deferred (days/weeks) | Build ground truth, improve agents | User feedback |

Trying to solve all three with one mechanism either over-invests on cheap tasks or under-invests on expensive ones.

### Trade-offs accepted

- **We accept that MVP will ship without semantic accuracy checks.** Schema + automated checks catch structural garbage but not plausible-but-wrong data. This is acceptable because users will visually validate results anyway.
- **We accept LLM eval cost for high-value workflows only.** The trigger should be configurable per workflow template, not universal.
- **We accept that user feedback will be sparse.** Design for low response rates (5-15%). Make feedback optional, frictionless, and useful even at low volume.

## Quality Check Implementation Sketch

### Tier 1: Immediate (ship at MVP)

```
QualityCheckResult {
  schema_valid: boolean
  completeness_score: float     // % of required fields non-null and non-empty
  freshness_ok: boolean         // dates in expected range (future for events)
  source_urls_valid: boolean[]  // HTTP HEAD check, 2xx = valid
  dedup_count: int              // duplicates found across agent results
  source_diversity: float       // unique domains / total results
  overall_score: float          // weighted composite
  flags: string[]               // human-readable issues: ["3 results have past dates"]
}
```

**Automated checks by category:**

| Check | Method | Cost |
|-------|--------|------|
| Schema compliance | JSON Schema validation | ~0ms |
| Field completeness | Null/empty/placeholder detection | ~0ms |
| Date freshness | Compare against task context (future events, recent news) | ~0ms |
| URL liveness | Async HTTP HEAD with 3s timeout | ~100ms per URL |
| Deduplication | Fuzzy match on name + date + location | ~10ms per pair |
| Source diversity | Count unique base domains across results | ~0ms |

**Composite scoring formula (configurable per task type):**

```
overall_score = (
  0.30 * completeness_score +
  0.20 * freshness_score +
  0.20 * url_validity_rate +
  0.15 * (1 - duplicate_rate) +
  0.15 * source_diversity
)
```

Results below a configurable threshold (e.g., 0.4) get flagged for review or filtered.

### Tier 2: LLM Evaluation (post-MVP, selective)

Triggered when:
- Workflow is marked `high_value: true`
- Overall Tier 1 score is ambiguous (0.4-0.7 range)
- User has opted into enhanced quality mode

```
Prompt template:
"You are evaluating data sourcing results. The task was: '{task_description}'.
Here are the results: {results_json}.

Rate each result 1-5 on:
- Relevance: Does this match what was asked for?
- Accuracy: Do the details look correct and consistent?
- Completeness: Is there enough information to be useful?

Flag any results that look fabricated or contradictory.
Return structured JSON."
```

**Bias mitigation:**
- Use a different model or temperature than the sourcing agent
- Include "I cannot verify" as a valid response -- don't force confidence
- Cross-reference: if 3 agents found the same event independently, confidence rises regardless of LLM eval
- Track LLM eval accuracy against user feedback over time (calibration)

**Cost control:**
- Evaluate result sets, not individual results (batch the prompt)
- Cache evaluations for identical result sets
- Budget cap per workflow execution

### Tier 3: User Feedback (continuous)

**Feedback surfaces (low friction):**

1. **Result-level**: Thumbs up/down on individual results in the UI
2. **Set-level**: "Were these results useful?" after viewing a complete result set
3. **Implicit signals**: Did the user click through to source URLs? Did they re-run the same task with different parameters? Did they manually add results the agents missed?

**Feedback storage:**

```
FeedbackRecord {
  workflow_id: string
  task_type: string
  agent_id: string
  result_id: string
  signal: "positive" | "negative" | "flag"
  reason?: string              // optional free text
  timestamp: datetime
  automated_score: float       // Tier 1 score at time of delivery
  llm_eval_score?: float       // Tier 2 score if available
}
```

**Feedback loop:**
- Aggregate feedback per agent + source combination
- Surface patterns: "Agent X consistently gets low ratings for music events"
- Feed into performance tracker for agent selection/weighting
- Use as labeled data to calibrate Tier 1 thresholds and Tier 2 prompts

## Architecture Integration

```
[Fan-out Agents] --> [Raw Results]
                          |
                    [Tier 1: Schema + Auto Checks]
                          |
                    [Score + Flag]
                          |
                   /              \
          score >= 0.7          score 0.4-0.7          score < 0.4
              |                      |                      |
         [Deliver]          [Tier 2: LLM Eval?]       [Filter/Warn]
              |                      |                      |
         [User sees           [Re-score, deliver       [Log for
          results]             with confidence]         analysis]
              |                      |
         [Tier 3: Optional feedback collection]
              |
         [Performance Tracker]
```

## Task-Type-Specific Quality Definitions

Different data sourcing tasks need different check configurations:

| Task Type | Key Checks | Freshness Rule | Dedup Strategy |
|-----------|------------|----------------|----------------|
| Events | Date in future, venue exists | Must be future | Name + date + city |
| Businesses | Address valid, phone format | N/A | Name + address |
| News/Articles | Publication date recent | Last N days | URL exact match |
| People/Contacts | Email format, role present | N/A | Name + org |
| Products/Prices | Price > 0, currency valid | Last 24h for prices | SKU or name + vendor |

## Phasing

| Phase | What | When |
|-------|------|------|
| MVP | Tier 1 (schema + automated checks) with composite score | Week 1-2 |
| V1.1 | Tier 3 (user feedback UI + storage) | Week 3-4 |
| V1.2 | Tier 2 (selective LLM eval) + feedback calibration | Week 5-8 |
| V2 | Closed loop: feedback adjusts Tier 1 thresholds automatically | Quarter 2 |

## Key Architectural Decision

**ADR: Quality checks run in the merge step, not in individual agents.**

- **Context**: We could have each agent self-assess quality, or assess at merge time.
- **Decision**: Assess at merge. Agents return raw results; the orchestrator runs quality checks.
- **Rationale**: (1) Cross-agent checks like dedup and source diversity require seeing all results together. (2) Agents should not grade their own work. (3) Centralizing quality logic makes it easier to update thresholds without redeploying agents.
- **Consequence**: Merge step becomes more complex but is the single point of quality enforcement. Agent development stays simple -- just find and return data.
